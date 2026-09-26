"""What the window and the tray show and what their actions do, without any Qt.

A user is either in no network, or in exactly one, which SteamVirtualLAN
remembers between runs (state.py). In a network, this PC is:

- online: in the network's Steam lobby, connected to the other online members
  and with the virtual adapter up;
- offline: remembered as a member, with its address kept, but not connected.
  Go Offline does this, and so does exiting the app, except that after an
  exit the next start goes back online by itself.

Leave Network is the only way out of a network: it tells the network so that
the address is freed, and forgets the network.

Losing the connection by accident is different from going offline. When this
PC can't reach Steam any more (ISteamUser::BLoggedOn), or finds itself no
longer in the network's lobby, it doesn't know anything about the other
members, so it doesn't conclude anything: the member list stays as it was last
seen, behind a "Restoring connection..." overlay, and the adapter stays up.

    online --(connection lost)--> RECONNECTING --(Steam back)--> RESTORING_NETWORK
    RESTORING_NETWORK --(in the lobby again)--> online
    RESTORING_NETWORK --(Steam lost again)--> RECONNECTING

RECONNECTING lasts as long as Steam is unreachable. RESTORING_NETWORK gets
back into the lobby, trying again with a growing delay, and gives up after
RESTORE_TIMEOUT, or at once when Steam says the lobby no longer exists. Only a
user's Go Offline stops all of this.
"""

import enum
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from steamlan.adapter.ipv4 import VIRTUAL_NETWORK
from steamlan.adapter.launcher import AdapterHelper, State
from steamlan.app import access, roster
from steamlan.app.network import NO_MEMBERS_ONLINE, MemberView, NetworkSession
from steamlan.app.state import SavedNetwork, StateStore
from steamlan.app.steamworks import open_steam
from steamlan.steam import (
    ConnectStringJoinRequest,
    LobbyJoinRequest,
    SteamAPILoadError,
    SteamClient,
    SteamError,
    SteamInitError,
    decode_lobby_event,
)
from steamlan.steam.client import read_lobby_created, read_lobby_enter

log = logging.getLogger(__name__)

MAX_MEMBERS = 8
# How long to wait for Steam to answer CreateLobby or JoinLobby.
CALL_TIMEOUT = 15.0
# How long Leave Network keeps trying to tell the network before giving up.
FAREWELL_TIMEOUT = 15.0
# Once Steam is back, how long to keep trying to get back into the lobby.
RESTORE_TIMEOUT = 90.0
# Seconds before each further attempt to get back into the lobby.
RESTORE_BACKOFF = (2.0, 4.0, 8.0, 15.0)

_STEAM_ERRORS = (SteamAPILoadError, SteamInitError, SteamError)

NETWORK_GONE = (
    "Network is no longer available. Steam removes a network once all of its members are offline."
)
RESTORE_FAILED = "Could not restore the connection to the network. Go Online to try again."


class Screen(enum.Enum):
    HOME = "home"
    JOIN = "join"
    NETWORK = "network"


class Presence(enum.Enum):
    """Whether this PC is available in its network: SteamVirtualLAN's own
    Online and Offline, unrelated to the Steam friends status."""

    NONE = "none"  # not in a network
    OFFLINE = "offline"  # chosen by the user, or given up on
    CONNECTING = "connecting"
    ONLINE = "online"
    RESTORING = "restoring"  # the connection was lost by accident


class Link(enum.Enum):
    """Why a member that wants to be online isn't, while it recovers."""

    RECONNECTING = "reconnecting"  # this PC can't reach Steam
    RESTORING_NETWORK = "restoring"  # Steam is back; getting back into the lobby


@dataclass(frozen=True)
class View:
    screen: Screen
    steam_ready: bool
    steam_status: str
    steam_tone: str = "pending"
    busy: str = ""
    error: str = ""
    presence: Presence = Presence.NONE
    lobby_id: int = 0
    access_code: str = ""
    can_invite: bool = False
    network_status: str = ""
    network_tone: str = "neutral"
    # e.g. "2 of 3 online"
    online_summary: str = ""
    # The local virtual adapter: starting, waiting for an address, or ready.
    adapter_status: str = ""
    adapter_tone: str = "neutral"
    members: tuple[MemberView, ...] = ()
    # While restoring: shown over the network page, whose members are then
    # only as last seen.
    overlay: str = ""
    overlay_detail: str = ""

    @property
    def tray_status(self) -> str:
        """One line for the tray menu and tooltip."""
        if not self.steam_ready:
            return "Steam is not running"
        if self.presence is Presence.ONLINE:
            address = next((m.address for m in self.members if m.is_you), "")
            return f"Online · {address}" if address else "Online"
        if self.presence is Presence.CONNECTING:
            return "Connecting..."
        if self.presence is Presence.RESTORING:
            return "Restoring connection..."
        if self.presence is Presence.OFFLINE:
            return "Offline"
        return "Not in a network"


@dataclass
class _Pending:
    # "create", "join" (a new network), "rejoin" (the saved one) or
    # "farewell" (the saved one, only to say it is leaving).
    kind: str
    api_call: int
    started: float
    lobby_id: int = 0
    access_code: str = ""


def join_error_message(error: SteamError) -> str:
    text = str(error)
    if "DOESNT_EXIST" in text:
        return "Lobby not found"
    if "FULL" in text:
        return "That lobby is full"
    if any(reason in text for reason in ("NOT_ALLOWED", "BANNED", "LIMITED", "BLOCKED")):
        return "You are not allowed to join that lobby"
    return "Could not join lobby"


def _cleanup(action: Callable[..., object], *args: object) -> None:
    """Run one cleanup step; a failure must not stop the others."""
    try:
        action(*args)
    except SteamError as exc:
        log.warning("Cleanup: %s", exc)


class AppController:
    def __init__(
        self,
        open_steam: Callable[[], SteamClient] = open_steam,
        clock: Callable[[], float] = time.monotonic,
        make_helper: Callable[[], AdapterHelper] | None = None,
        store: StateStore | None = None,
    ):
        self._open_steam = open_steam
        self.clock = clock
        self._make_helper = make_helper or AdapterHelper
        self.store = store or StateStore()
        self.steam: SteamClient | None = None
        self.persona = ""
        self.steam_id = 0
        self.steam_error = ""
        self.screen = Screen.HOME
        self.busy = ""
        self.error = ""
        # The network this user is a member of, as saved; None when in none.
        self.saved: SavedNetwork | None = None
        # The network while online in it (or while saying goodbye to it).
        self.network: NetworkSession | None = None
        self.network_id = ""
        # The elevated helper that owns the virtual adapter while online.
        self.helper: AdapterHelper | None = None
        self._pending: _Pending | None = None
        self._lobby_id = 0
        self._listen_socket = 0
        # Set while Leave Network is still telling the network, which was this.
        self._farewell_until: float | None = None
        self._leaving: SavedNetwork | None = None
        # JoinLobby calls given up on (Go Offline while connecting): if Steam
        # still lets us in, leave again. api_call -> lobby ID.
        self._abandoned: dict[int, int] = {}
        # Set while recovering from an accidental loss of the connection.
        self.link: Link | None = None
        # The member list as last seen, shown while recovering.
        self._frozen: tuple[MemberView, ...] = ()
        self._restore_started = 0.0
        self._next_attempt = 0.0
        self._attempts = 0

    # Starting and stopping

    def start(self) -> None:
        """Start the Steam API, then go back to the saved network, if any."""
        if self.steam is not None:
            return
        self.steam_error = ""
        try:
            steam = self._open_steam()
            self.persona = steam.persona_name
            self.steam_id = steam.steam_id
        except _STEAM_ERRORS as exc:
            log.warning("Could not start Steam: %s", exc)
            self.steam_error = str(exc)
            return
        self.steam = steam
        self._restore()

    def shutdown(self) -> None:
        """Exit: stop the adapter and every connection and leave the Steam lobby,
        but stay a member of the network. The next start goes back online if
        this PC is online now."""
        if self.network is not None and self._farewell_until is None and self.network.joined:
            self._save(self._snapshot(online=True))
        self._close_network()
        self._pending = None
        self.busy = ""
        self._stop_recovering()
        if self.steam is not None:
            self.steam.close()
            self.steam = None

    def _restore(self) -> None:
        saved = self.store.load(self.steam_id)
        if saved is None:
            return
        log.info("Saved network: lobby %s (%s)", saved.lobby_id, saved.network_id)
        self.saved = saved
        self.screen = Screen.NETWORK
        if saved.online:
            self._go_back_online()

    def _go_back_online(self) -> None:
        if self._logged_on():
            self._start_join(self.saved.lobby_id, self.saved.access_code, "rejoin")
        else:
            self._lose_connection(logged_on=False)

    # What the UI shows

    def presence(self) -> Presence:
        if self._farewell_until is not None:
            return Presence.NONE
        if self.link is not None:
            return Presence.RESTORING
        if self.network is not None:
            return Presence.ONLINE if self.network.joined else Presence.CONNECTING
        if self._pending is not None and self._pending.kind == "rejoin":
            return Presence.CONNECTING
        if self.saved is not None:
            return Presence.OFFLINE
        return Presence.NONE

    def view(self) -> View:
        if self.steam is not None:
            steam_status, steam_tone = f"Signed in to Steam as {self.persona}", "ok"
        elif self.steam_error:
            steam_status, steam_tone = self.steam_error, "error"
        else:
            steam_status, steam_tone = "Starting Steam...", "pending"
        base = {
            "steam_ready": self.steam is not None,
            "steam_status": steam_status,
            "steam_tone": steam_tone,
            "error": self.error,
        }

        presence = self.presence()
        if presence is Presence.NONE:
            return View(self.screen, busy=self.busy, **base)

        network, saved = self.network, self.saved
        adapter_status, adapter_tone = self._adapter_status(network)
        if presence is Presence.RESTORING:
            detail = (
                "SteamVirtualLAN is reconnecting"
                if self.link is Link.RECONNECTING
                else "Rejoining your network..."
            )
            return View(
                Screen.NETWORK,
                presence=presence,
                lobby_id=saved.lobby_id,
                access_code=access.format_access_code(saved.access_code),
                network_status="Restoring connection...",
                network_tone="pending",
                adapter_status=adapter_status,
                adapter_tone=adapter_tone,
                members=self._frozen,
                overlay="Restoring connection...",
                overlay_detail=detail,
                **base,
            )
        if network is not None:
            status, tone = network.status()
            members = tuple(network.members())
            code = network.access_code
            lobby_id = network.lobby_id
        else:
            members = self._offline_members()
            code = saved.access_code
            lobby_id = saved.lobby_id
            if presence is Presence.CONNECTING:
                status, tone = "Connecting to the network...", "pending"
            else:
                status, tone = "Offline", "neutral"
        in_roster = [member for member in members if member.address]
        online = sum(member.online for member in in_roster)
        if presence is not Presence.ONLINE:
            online = 0
        return View(
            Screen.NETWORK,
            presence=presence,
            lobby_id=lobby_id,
            access_code=access.format_access_code(code) if code else "",
            can_invite=presence is Presence.ONLINE and bool(code),
            network_status=status,
            network_tone=tone,
            online_summary=f"{online} of {len(in_roster)} online" if in_roster else "",
            adapter_status=adapter_status,
            adapter_tone=adapter_tone,
            members=members,
            **base,
        )

    def _offline_members(self, status: str = "Offline") -> tuple[MemberView, ...]:
        saved = self.saved
        names = dict(saved.names)
        views = []
        for steam_id, address in sorted(saved.members, key=lambda item: item[1]):
            is_you = steam_id == self.steam_id
            name = self.persona if is_you else names.get(steam_id, "")
            if not name and self.steam is not None:
                try:
                    name = self.steam.friend_persona_name(steam_id)
                except SteamError:
                    name = ""
            views.append(
                MemberView(
                    steam_id,
                    name or f"Steam user {str(steam_id)[-4:]}",
                    is_you=is_you,
                    online=False,
                    status=status,
                    tone="neutral",
                    address=str(address),
                )
            )
        views.sort(key=lambda view: not view.is_you)
        return tuple(views)

    def _adapter_status(self, network: NetworkSession | None) -> tuple[str, str]:
        helper = self.helper
        if helper is None or network is None:
            return "", "neutral"
        if helper.state is State.STARTING:
            return helper.progress, "pending"
        if helper.state is State.FAILED:
            return helper.error, "error"
        if helper.state is not State.READY:
            return "", "neutral"
        address = network.local_address
        if address is None:
            return "Waiting for an address from the network", "pending"
        if helper.address != address:
            return f"Setting up {address}...", "pending"
        return f"Virtual network ready: {address}", "ok"

    # Actions

    def show_join(self) -> None:
        if self._idle():
            self.screen = Screen.JOIN
            self.error = ""

    def show_home(self) -> None:
        if self._idle():
            self.screen = Screen.HOME
            self.error = ""

    def create_lobby(self) -> None:
        if not self._idle():
            return
        self.error = ""
        try:
            self._open_listen_socket()
            api_call = self.steam.request_create_lobby(MAX_MEMBERS)
        except SteamError as exc:
            log.warning("CreateLobby: %s", exc)
            self._close_network()
            self.error = "Could not create lobby"
            return
        self._pending = _Pending("create", api_call, self.clock())
        self.busy = "Creating lobby..."

    def join(self, lobby_text: str, code_text: str) -> None:
        if not self._idle():
            return
        try:
            lobby_id = access.parse_lobby_id(lobby_text)
            code = access.normalize_access_code(code_text)
        except ValueError as exc:
            self.error = str(exc)
            return
        self._start_join(lobby_id, code, "join")

    def go_online(self) -> None:
        """Go back online in the saved network."""
        saved = self.saved
        if saved is None or self.steam is None or self.presence() is not Presence.OFFLINE:
            return
        if self._pending is not None:
            return
        self.error = ""
        self._save(saved.with_online(True))
        self._go_back_online()

    def go_offline(self) -> None:
        """Stop being available in the network, but stay a member of it.
        Nothing reconnects by itself until Go Online."""
        presence = self.presence()
        if presence not in (Presence.ONLINE, Presence.CONNECTING, Presence.RESTORING):
            return
        if self.network is not None and self.network.joined:
            self._save(self._snapshot(online=False))
        elif self.saved is not None:
            self._save(self.saved.with_online(False))
        self._abandon()
        self._stop_recovering()
        self.busy = ""
        self.error = ""
        self._close_network()
        if self.saved is None:
            # Was still joining a new network: there is nothing to be offline in.
            self.screen = Screen.HOME

    def leave_network(self) -> None:
        """Leave the network for good: free this PC's address and forget it."""
        saved, network, pending = self.saved, self.network, self._pending
        if self._farewell_until is not None or (saved is None and network is None):
            return
        self._save(None)
        self._leaving = saved
        self.screen = Screen.HOME
        self.error = ""
        if self.link is not None:
            # There is no way to tell the network without a connection.
            log.warning("Left while disconnected; the address stays taken in the network")
            self._abandon()
            self._stop_recovering()
            self._close_network()
            self._leaving = None
        elif network is not None and (network.joined or saved is not None):
            self._begin_farewell()
        elif network is not None:
            # Was still joining a new network: nobody there knows this member.
            self._close_network()
        elif pending is not None:
            # Going back online: go on into the lobby, only to tell the network.
            pending.kind = "farewell"
            self._begin_farewell()
        elif self.steam is not None:
            # Offline: go into the lobby just to tell the network.
            self._start_join(saved.lobby_id, saved.access_code, "farewell")
            if self._pending is not None:
                self._farewell_until = self.clock() + FAREWELL_TIMEOUT

    def open_invite(self) -> str:
        """Open Steam's overlay invite dialog; raises ValueError with a message to show.

        The invites carry the lobby ID and access code, so the friends picked in
        Steam join without typing anything.
        """
        if self.steam is None:
            raise ValueError("Steam is not running")
        network = self.network
        if network is None or self.presence() is not Presence.ONLINE:
            raise ValueError("Go online to invite friends")
        if not network.access_code:
            raise ValueError("The access code isn't known yet")
        connect = access.invite_connect_string(network.lobby_id, network.access_code)
        try:
            if not self.steam.overlay_enabled:
                raise ValueError(
                    "The Steam overlay isn't available. Check that it is enabled in "
                    "Steam's settings; right after starting it can take a few seconds."
                )
            self.steam.open_invite_dialog(connect)
        except SteamError as exc:
            log.warning("Invite dialog: %s", exc)
            raise ValueError("Steam could not open the invite dialog") from None
        return "Pick friends to invite in the Steam overlay"

    def overlay_needs_present(self) -> bool:
        """Whether the window should repaint so the Steam overlay can draw."""
        if self.steam is None:
            return False
        try:
            return self.steam.overlay_needs_present()
        except SteamError:
            return False

    # Pumping Steam

    def tick(self) -> None:
        """Pump Steam once. Called regularly by the window, visible or not."""
        if self.steam is None:
            return
        try:
            self._tick()
        except SteamError as exc:
            log.warning("Steam error: %s", exc)

    def _tick(self) -> None:
        callbacks = self.steam.run_callbacks()
        for callback in [c for c in callbacks if c.api_call in self._abandoned]:
            callbacks.remove(callback)
            _cleanup(self.steam.leave_lobby, self._abandoned.pop(callback.api_call))

        pending = self._pending
        if pending is not None:
            result = next((c for c in callbacks if c.api_call == pending.api_call), None)
            if result is not None:
                callbacks.remove(result)
                self._pending = None
                self.busy = ""
                self._finish(pending, result)
            elif self.clock() - pending.started > CALL_TIMEOUT:
                self._pending = None
                self.busy = ""
                self._call_failed(pending, "Steam did not answer in time")

        if self._farewell_until is not None:
            if self.network is not None:
                self.network.process(callbacks)
                self._farewell()
            return
        if self.saved is not None and (self.network is not None or self.link is not None):
            self._watch_connection()

        network = self.network
        if network is not None:
            network.process(callbacks)
            self._forward_packets()
            if self.link is None:
                self._check_network()
            elif self.helper is not None and self.helper.state is State.FAILED:
                self._go_offline_with(self.helper.error)
        elif self._pending is None and self.link is None:
            self._handle_invites(callbacks)

    # Recovering from a lost connection

    def _logged_on(self) -> bool:
        try:
            return self.steam.logged_on
        except SteamError:
            return False

    def _in_lobby(self, lobby_id: int) -> bool:
        """Whether Steam still has this PC in the lobby."""
        try:
            return self.steam_id in self.steam.lobby_members(lobby_id) and bool(
                self.steam.lobby_owner(lobby_id)
            )
        except SteamError:
            return False

    def _watch_connection(self) -> None:
        logged_on = self._logged_on()
        if self.link is None:
            network = self.network
            if network is not None and not (logged_on and self._in_lobby(network.lobby_id)):
                self._lose_connection(logged_on)
            return
        if not logged_on:
            if self.link is Link.RESTORING_NETWORK:
                log.info("Lost the connection to Steam again")
                self._abandon(leave=False)
                self.link = Link.RECONNECTING
                if self.network is not None:
                    self.network.suspend()
            return

        now = self.clock()
        if self.link is Link.RECONNECTING:
            log.info("Steam is back; restoring the network")
            self._begin_restoring(now)
        if self._pending is not None:
            return
        network = self.network
        if network is not None and self._in_lobby(network.lobby_id):
            self._restored()
        elif now - self._restore_started > RESTORE_TIMEOUT:
            self._give_up(RESTORE_FAILED)
        elif now >= self._next_attempt:
            delay = RESTORE_BACKOFF[min(self._attempts, len(RESTORE_BACKOFF) - 1)]
            self._attempts += 1
            self._next_attempt = now + delay
            self._start_join(self.saved.lobby_id, self.saved.access_code, "restore")

    def _lose_connection(self, logged_on: bool) -> None:
        """The connection was lost by accident: keep everything, and recover."""
        network = self.network
        if self.link is None:
            if network is not None:
                log.info("Lost the connection to the network")
                members = network.members()
            else:
                members = self._offline_members(status="\u2014")
            # As last seen: nothing about them is known now.
            self._frozen = tuple(replace(member, latency="") for member in members)
        if network is not None:
            network.suspend()
        if logged_on:
            self._begin_restoring(self.clock())
        else:
            self.link = Link.RECONNECTING

    def _begin_restoring(self, now: float) -> None:
        self.link = Link.RESTORING_NETWORK
        self._restore_started = now
        self._next_attempt = now
        self._attempts = 0

    def _restored(self) -> None:
        log.info("Back in the network")
        self.network.resume()
        self._stop_recovering()
        self.error = ""

    def _stop_recovering(self) -> None:
        self.link = None
        self._frozen = ()

    def _give_up(self, reason: str) -> None:
        """Stop trying; the membership is kept, and the next start tries again."""
        log.warning("Could not restore the network: %s", reason)
        self._abandon()
        self._stop_recovering()
        self._close_network()
        self.error = reason

    def _check_network(self) -> None:
        network = self.network
        if network.joined:
            snapshot = self._snapshot(online=True)
            if snapshot != self.saved:
                self._save(snapshot)
        if network.refused and not network.joined:
            self._refused(network.refused)
        elif self.helper is not None and self.helper.state is State.FAILED:
            # Without its adapter this PC can't take part.
            reason = self.helper.error
            if self.saved is not None:
                self._go_offline_with(reason)
            else:
                self._close_network()
                self.screen = Screen.JOIN
                self.error = reason

    def _refused(self, reason: str) -> None:
        saved = self.saved
        self._close_network()
        if saved is None:
            # A new member that wasn't let in.
            self.screen = Screen.JOIN
            self.error = reason
        elif reason in (NO_MEMBERS_ONLINE, "The network did not answer"):
            self._save(saved.with_online(False))
            self.error = f"{reason}. Try going online again later."
        else:
            # The network doesn't know this member any more.
            self._save(None)
            self.screen = Screen.HOME
            self.error = "You are no longer a member of that network"

    def _go_offline_with(self, reason: str) -> None:
        if self.network is not None and self.network.joined:
            self._save(self._snapshot(online=False))
        else:
            self._save(self.saved.with_online(False))
        self._abandon()
        self._stop_recovering()
        self._close_network()
        self.error = reason

    def _handle_invites(self, callbacks) -> None:
        for callback in callbacks:
            event = decode_lobby_event(callback)
            if isinstance(event, ConnectStringJoinRequest):
                # The user accepted one of our overlay invites.
                try:
                    lobby_id, code = access.parse_invite_connect_string(event.connect)
                except ValueError:
                    log.warning("Ignored an invite that isn't ours: %r", event.connect)
                    continue
            elif isinstance(event, LobbyJoinRequest):
                # A plain Steam lobby invite or "Join Game"; such members still
                # need the access code, which the network asks for.
                log.info("Join requested for lobby %s", event.lobby_id)
                lobby_id, code = event.lobby_id, ""
            else:
                continue
            if self.saved is None:
                self._start_join(lobby_id, code, "join")
            elif lobby_id == self.saved.lobby_id:
                self.go_online()
            else:
                self.error = "You are already in a network. Leave it to join another one."
            break

    def _forward_packets(self) -> None:
        """Move IP packets between the local adapter and the other members."""
        network, helper = self.network, self.helper
        if helper is None:
            return
        # Windows -> adapter -> helper -> the member that owns the destination.
        for packet in helper.poll():
            network.send_packet(packet)
        # Other members -> helper -> adapter -> Windows. Packets that arrive
        # before the adapter has its address are dropped, as IP allows.
        received = network.take_packets()
        if helper.ready and network.local_address is not None:
            helper.set_address(network.local_address)
            for packet in received:
                helper.send_packet(packet)

    # Joining and leaving

    def _idle(self) -> bool:
        return (
            self.steam is not None
            and self.saved is None
            and self.network is None
            and self._pending is None
        )

    def _open_listen_socket(self) -> None:
        """Listen for the other members before entering their lobby: they
        connect as soon as Steam tells them about the new member."""
        if not self._listen_socket:
            self._listen_socket = self.steam.create_listen_socket(0)

    def _start_join(self, lobby_id: int, code: str, kind: str) -> None:
        self.error = ""
        try:
            self._open_listen_socket()
            api_call = self.steam.request_join_lobby(lobby_id)
        except SteamError as exc:
            log.warning("JoinLobby: %s", exc)
            self._call_failed(_Pending(kind, 0, 0.0, lobby_id, code), "Could not join lobby")
            return
        self._pending = _Pending(kind, api_call, self.clock(), lobby_id, code)
        self.busy = "Joining..." if kind == "join" else ""
        if kind == "farewell":
            self.busy = "Leaving the network..."

    def _call_failed(self, pending: _Pending, message: str) -> None:
        if pending.kind == "restore":
            # Tried again later, until RESTORE_TIMEOUT.
            log.info("Could not get back into the network yet: %s", message)
            if "not allowed" in message:
                self._give_up(f"Could not go online: {message}")
            return
        self._close_network()
        if pending.kind == "farewell":
            self._end_farewell()
            log.info("Could not tell the network about leaving: %s", message)
        elif pending.kind == "rejoin" and "not allowed" in message:
            self._save(self.saved.with_online(False))
            self.error = f"Could not go online: {message}"
        elif pending.kind == "rejoin":
            # Probably the connection; keep trying.
            self._lose_connection(self._logged_on())
        else:
            self.error = message

    def _finish(self, pending: _Pending, result) -> None:
        steam = self.steam
        if pending.kind == "create":
            try:
                lobby_id = read_lobby_created(result)
            except SteamError as exc:
                log.warning("%s", exc)
                self._close_network()
                self.error = "Could not create lobby"
                return
            self._lobby_id = lobby_id
            self.network_id = access.generate_network_id()
            first = {steam.steam_id: VIRTUAL_NETWORK[1]}
            try:
                steam.set_lobby_data(lobby_id, access.LOBBY_MARKER_KEY, access.LOBBY_MARKER_VALUE)
                steam.set_lobby_data(lobby_id, access.NETWORK_ID_KEY, self.network_id)
            except SteamError as exc:
                log.warning("%s", exc)
                self._close_network()
                self.error = "Could not create lobby"
                return
            self._enter(lobby_id, access.generate_access_code(), known=first)
            return

        try:
            lobby_id = read_lobby_enter(result, pending.lobby_id)
        except SteamError as exc:
            log.warning("%s", exc)
            if pending.kind in ("rejoin", "restore") and "DOESNT_EXIST" in str(exc):
                self._forget_gone_network()
            else:
                self._call_failed(pending, join_error_message(exc))
            return
        self._lobby_id = lobby_id
        network_id = steam.lobby_data(lobby_id, access.NETWORK_ID_KEY)
        ours = steam.lobby_data(
            lobby_id, access.LOBBY_MARKER_KEY
        ) == access.LOBBY_MARKER_VALUE and access.is_network_id(network_id)
        saved = self._leaving if pending.kind == "farewell" else self.saved
        if pending.kind in ("rejoin", "restore", "farewell") and (
            not ours or saved is None or network_id != saved.network_id
        ):
            self._close_network()
            if pending.kind == "farewell":
                self._end_farewell()
            else:
                self._forget_gone_network()
            return
        if not ours:
            self._close_network()
            self.error = "That lobby is not a SteamVirtualLAN network"
            return
        self.network_id = network_id
        if pending.kind == "join":
            self._enter(lobby_id, pending.access_code)
            return
        if pending.kind == "restore" and self.network is not None:
            # The same session carries on, with the same adapter.
            self._restored()
            return
        if pending.kind == "restore":
            self._stop_recovering()
        known = dict(saved.members)
        self._enter(
            lobby_id,
            saved.access_code,
            known=known,
            names=dict(saved.names),
            preferred=known.get(self.steam_id),
            adapter=pending.kind != "farewell",
        )

    def _forget_gone_network(self) -> None:
        self._abandon()
        self._stop_recovering()
        self._close_network()
        log.info("The saved network %s no longer exists", self.saved.lobby_id)
        self._save(None)
        self.screen = Screen.HOME
        self.error = NETWORK_GONE

    def _enter(
        self,
        lobby_id: int,
        access_code: str,
        known: roster.Roster | None = None,
        names: dict[int, str] | None = None,
        preferred=None,
        adapter: bool = True,
    ) -> None:
        self.network = NetworkSession(
            self.steam,
            lobby_id,
            self._listen_socket,
            access_code,
            self.clock,
            known=known,
            names=names,
            preferred=preferred,
        )
        if self._farewell_until is not None:
            self.busy = "Leaving the network..."
            return
        self.screen = Screen.NETWORK
        if not adapter:
            return
        if self.helper is not None:
            # Still up from before; never a second adapter or helper.
            return
        # The adapter comes up while Steam connects the members; the UAC
        # prompt, if any, appears now.
        self.helper = self._make_helper()
        try:
            self.helper.start()
        except OSError as exc:
            log.warning("Network helper: %s", exc)
            if self.saved is not None:
                self._go_offline_with("Could not start the virtual network")
            else:
                self._close_network()
                self.screen = Screen.HOME
                self.error = "Could not start the virtual network"
            return
        self.error = ""

    def _begin_farewell(self) -> None:
        """Stop taking part at once, then stay in the lobby only until the
        network knows this member left."""
        helper, self.helper = self.helper, None
        if helper is not None:
            helper.close()
        self._farewell_until = self.clock() + FAREWELL_TIMEOUT
        self.busy = "Leaving the network..."
        if self.network is not None:
            self._farewell()

    def _abandon(self, leave: bool = True) -> None:
        """Forget the pending Steam call. With leave, a lobby it still gets
        this PC into is left again; without, being in it is welcome (restoring
        notices it)."""
        pending, self._pending = self._pending, None
        if pending is not None and pending.kind != "create" and pending.api_call and leave:
            self._abandoned[pending.api_call] = pending.lobby_id

    def _farewell(self) -> None:
        told = self.network.announce_leave()
        if told or self.clock() > self._farewell_until:
            if not told:
                log.warning("Left without telling the network; its address stays taken")
            self._close_network(linger=True)
            self._end_farewell()

    def _end_farewell(self) -> None:
        self._farewell_until = None
        self._leaving = None
        self.busy = ""

    def _close_network(self, linger: bool = False) -> None:
        """Stop forwarding and remove the adapter, then close peer connections,
        leave the lobby and close the listen socket."""
        helper, self.helper = self.helper, None
        if helper is not None:
            # Waits (briefly) until the helper has removed the adapter.
            helper.close()
        steam = self.steam
        network, self.network = self.network, None
        lobby_id, self._lobby_id = self._lobby_id, 0
        listen_socket, self._listen_socket = self._listen_socket, 0
        if steam is None:
            return
        if network is not None:
            _cleanup(network.close, linger)
        if lobby_id:
            _cleanup(steam.leave_lobby, lobby_id)
        if listen_socket:
            _cleanup(steam.close_listen_socket, listen_socket)

    # Saved state

    def _snapshot(self, online: bool) -> SavedNetwork:
        network = self.network
        return SavedNetwork(
            network.lobby_id,
            self.network_id,
            network.access_code,
            tuple(sorted(network.roster.items())),
            tuple(sorted(network.names.items())),
            online,
        )

    def _save(self, saved: SavedNetwork | None) -> None:
        self.saved = saved
        try:
            self.store.save(self.steam_id, saved)
        except OSError as exc:
            log.warning("Could not save the network state: %s", exc)
