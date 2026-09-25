"""What the window shows and what its buttons do, without any Qt."""

import enum
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from steamlan.app import access
from steamlan.app.network import MemberView, NetworkSession
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

_STEAM_ERRORS = (SteamAPILoadError, SteamInitError, SteamError)


class Screen(enum.Enum):
    HOME = "home"
    JOIN = "join"
    LOBBY = "lobby"


@dataclass(frozen=True)
class View:
    screen: Screen
    steam_ready: bool
    steam_status: str
    steam_tone: str = "pending"
    busy: str = ""
    error: str = ""
    lobby_id: int = 0
    access_code: str = ""
    is_host: bool = False
    network_status: str = ""
    network_tone: str = "neutral"
    members: tuple[MemberView, ...] = ()


@dataclass
class _Pending:
    kind: str  # "create" or "join"
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


def _cleanup(action: Callable[..., object], *args: int) -> None:
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
    ):
        self._open_steam = open_steam
        self.clock = clock
        self.steam: SteamClient | None = None
        self.persona = ""
        self.steam_error = ""
        self.screen = Screen.HOME
        self.busy = ""
        self.error = ""
        self.network: NetworkSession | None = None
        self._pending: _Pending | None = None
        self._lobby_id = 0
        self._listen_socket = 0

    def start(self) -> None:
        """Start the Steam API; on failure the home screen shows why."""
        if self.steam is not None:
            return
        self.steam_error = ""
        try:
            steam = self._open_steam()
        except _STEAM_ERRORS as exc:
            log.warning("Could not start Steam: %s", exc)
            self.steam_error = str(exc)
            return
        self.steam = steam
        self.persona = steam.persona_name

    def view(self) -> View:
        if self.steam is not None:
            steam_status, steam_tone = f"Signed in to Steam as {self.persona}", "ok"
        elif self.steam_error:
            steam_status, steam_tone = self.steam_error, "error"
        else:
            steam_status, steam_tone = "Starting Steam...", "pending"

        network = self.network
        if network is None:
            return View(
                self.screen,
                self.steam is not None,
                steam_status,
                steam_tone,
                busy=self.busy,
                error=self.error,
            )
        status, tone = network.status()
        return View(
            Screen.LOBBY,
            True,
            steam_status,
            steam_tone,
            lobby_id=network.lobby_id,
            access_code=access.format_access_code(network.access_code) if network.is_host else "",
            is_host=network.is_host,
            network_status=status,
            network_tone=tone,
            members=tuple(network.members()),
        )

    def show_join(self) -> None:
        if self.network is None and not self.busy:
            self.screen = Screen.JOIN
            self.error = ""

    def show_home(self) -> None:
        if self.network is None and not self.busy:
            self.screen = Screen.HOME
            self.error = ""

    def create_lobby(self) -> None:
        if self.steam is None or self.busy or self.network is not None:
            return
        self.error = ""
        try:
            api_call = self.steam.request_create_lobby(MAX_MEMBERS)
        except SteamError as exc:
            log.warning("CreateLobby: %s", exc)
            self.error = "Could not create lobby"
            return
        self._pending = _Pending("create", api_call, self.clock())
        self.busy = "Creating lobby..."

    def join(self, lobby_text: str, code_text: str) -> None:
        if self.steam is None or self.busy or self.network is not None:
            return
        try:
            lobby_id = access.parse_lobby_id(lobby_text)
            code = access.normalize_access_code(code_text)
        except ValueError as exc:
            self.error = str(exc)
            return
        self._start_join(lobby_id, code)

    def open_invite(self) -> str:
        """Open Steam's overlay invite dialog; raises ValueError with a message to show.

        The invites carry the lobby ID and access code, so the friends picked in
        Steam join without typing anything.
        """
        if self.steam is None:
            raise ValueError("Steam is not running")
        network = self.network
        if network is None:
            raise ValueError("Create a lobby first")
        if not network.is_host:
            raise ValueError("Only the host can invite")
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

    def leave(self) -> None:
        self._leave_network()
        self.screen = Screen.HOME

    def shutdown(self) -> None:
        self._leave_network()
        self._pending = None
        if self.steam is not None:
            self.steam.close()
            self.steam = None

    def tick(self) -> None:
        """Pump Steam once. Called regularly by the window."""
        if self.steam is None:
            return
        try:
            self._tick()
        except SteamError as exc:
            log.warning("Steam error: %s", exc)

    def _tick(self) -> None:
        callbacks = self.steam.run_callbacks()

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
                self.error = "Steam did not answer in time"

        if self.network is not None:
            self.network.process(callbacks)
            if self.network.refused and not self.network.joined:
                reason = self.network.refused
                self.leave()
                self.screen = Screen.JOIN
                self.error = reason
        elif self._pending is None:
            for callback in callbacks:
                event = decode_lobby_event(callback)
                if isinstance(event, ConnectStringJoinRequest):
                    # The user accepted one of our overlay invites.
                    try:
                        lobby_id, code = access.parse_invite_connect_string(event.connect)
                    except ValueError:
                        log.warning("Ignored an invite that isn't ours: %r", event.connect)
                        continue
                    self._start_join(lobby_id, code)
                    break
                if isinstance(event, LobbyJoinRequest):
                    # A plain Steam lobby invite or "Join Game"; such members still
                    # need the access code, which the host asks for.
                    log.info("Join requested for lobby %s", event.lobby_id)
                    self._start_join(event.lobby_id, "")
                    break

    def _start_join(self, lobby_id: int, code: str) -> None:
        self.error = ""
        try:
            api_call = self.steam.request_join_lobby(lobby_id)
        except SteamError as exc:
            log.warning("JoinLobby: %s", exc)
            self.error = "Could not join lobby"
            return
        self._pending = _Pending("join", api_call, self.clock(), lobby_id, code)
        self.busy = "Joining..."

    def _finish(self, pending: _Pending, result) -> None:
        steam = self.steam
        if pending.kind == "create":
            try:
                lobby_id = read_lobby_created(result)
            except SteamError as exc:
                log.warning("%s", exc)
                self.error = "Could not create lobby"
                return
            self._lobby_id = lobby_id
            try:
                steam.set_lobby_data(lobby_id, access.LOBBY_MARKER_KEY, access.LOBBY_MARKER_VALUE)
            except SteamError as exc:
                log.warning("%s", exc)
                self._leave_network()
                self.error = "Could not create lobby"
                return
            self._enter(lobby_id, steam.steam_id, access.generate_access_code())
            return

        try:
            lobby_id = read_lobby_enter(result, pending.lobby_id)
        except SteamError as exc:
            log.warning("%s", exc)
            self.error = join_error_message(exc)
            return
        self._lobby_id = lobby_id
        if steam.lobby_data(lobby_id, access.LOBBY_MARKER_KEY) != access.LOBBY_MARKER_VALUE:
            self._leave_network()
            self.error = "That lobby is not a SteamVirtualLAN network"
            return
        host_id = steam.lobby_owner(lobby_id)
        if host_id == steam.steam_id:
            # Steam made us the owner, so the host that held the access code is gone.
            self._leave_network()
            self.error = "The host has left that network"
            return
        self._enter(lobby_id, host_id, pending.access_code)

    def _enter(self, lobby_id: int, host_id: int, access_code: str) -> None:
        try:
            self._listen_socket = self.steam.create_listen_socket(0)
        except SteamError as exc:
            log.warning("%s", exc)
            self._leave_network()
            self.error = "Could not open a network connection"
            return
        self.network = NetworkSession(
            self.steam, lobby_id, self._listen_socket, host_id, access_code, self.clock
        )
        self.screen = Screen.LOBBY
        self.error = ""

    def _leave_network(self) -> None:
        """Close peer connections, leave the lobby and close the listen socket."""
        steam = self.steam
        network, self.network = self.network, None
        lobby_id, self._lobby_id = self._lobby_id, 0
        listen_socket, self._listen_socket = self._listen_socket, 0
        if steam is None:
            return
        if network is not None:
            _cleanup(network.close)
        if lobby_id:
            _cleanup(steam.leave_lobby, lobby_id)
        if listen_socket:
            _cleanup(steam.close_listen_socket, listen_socket)
