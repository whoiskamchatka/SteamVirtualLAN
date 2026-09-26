"""A SteamVirtualLAN network while this member is online in it.

Online means being in the network's Steam lobby: LobbySession keeps a direct
SteamNetworkingSockets connection to every other member there, and IP packets
go straight to the member that owns their destination address. They never
pass through a third member.

Every member of a network is equal. A few decisions still need exactly one
member to make them: admitting new members after checking their access code,
giving them addresses, and taking members out of the roster when they leave
for good. The member that makes them is called the coordinator here; it is an
internal responsibility and nothing a user sees.

The coordinator is always the Steam lobby owner (ISteamMatchmaking::
GetLobbyOwner). Steam guarantees that a lobby has exactly one owner among the
members currently in it. When the owner leaves the lobby or loses its
connection to Steam, Steam makes another member the owner by itself, and it
never hands ownership back to a member that returns. Every member reads the
owner from Steam, so all agree on who coordinates without an election of
their own. Only the owner can write lobby metadata, which is where the roster
lives (roster.py), so a new coordinator simply carries on with it.

Steam may make a member the owner that has not been admitted yet: one that
joined moments before, or that joined just as the owner left. Such an owner
knows neither the access code nor which roster to trust, so it hands
ownership to the online roster member with the lowest SteamID
(SetLobbyOwner). Members never take a roster from an owner they don't already
trust, so an owner that isn't a member can't give itself an address.
"""

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address

from steamlan.adapter.ipv4 import Delivery
from steamlan.app import access, roster, tunnel
from steamlan.app.latency import LatencyTracker, format_rtt
from steamlan.steam import (
    ChatMemberStateChange,
    LobbyMemberUpdate,
    LobbySession,
    SteamCallback,
    SteamClient,
    SteamError,
    decode_lobby_event,
)

log = logging.getLogger(__name__)

# How long a connected new member has to present its access code, and how
# long a member waits for the coordinator to answer its own.
AUTH_TIMEOUT = 20.0
# Wrong codes after which the coordinator stops listening to a SteamID.
MAX_FAILURES = 5
# How long an owner that isn't a member waits before handing ownership again.
HAND_OFF_INTERVAL = 5.0
# How often the traffic counters are logged (at debug level), if they changed.
TRAFFIC_LOG_INTERVAL = 60.0

NO_MEMBERS_ONLINE = "Nobody from this network is online right now"


@dataclass(frozen=True)
class MemberView:
    steam_id: int
    name: str
    is_you: bool
    online: bool
    status: str
    tone: str  # "ok", "pending", "error" or "neutral"
    address: str = ""  # virtual IP address, once the member has one
    # Round-trip time to a connected member, e.g. "24 ms"; "" otherwise.
    latency: str = ""


def display_id(steam_id: int) -> str:
    return f"Steam user {str(steam_id)[-4:]}"


class NetworkSession:
    def __init__(
        self,
        steam: SteamClient,
        lobby_id: int,
        listen_socket: int,
        access_code: str = "",
        clock: Callable[[], float] = time.monotonic,
        *,
        known: roster.Roster | None = None,
        names: dict[int, str] | None = None,
        preferred: IPv4Address | None = None,
    ):
        """
        access_code: the network's code as far as this member knows it: its
            own when it creates the network, the one it joins with, or the one
            it saved.
        known: the roster this member already trusts: its saved copy when it
            returns to a network, {} when it joins for the first time, and
            just itself when it creates the network.
        preferred: the address to ask for if it has to be admitted again.
        """
        self.steam = steam
        self.lobby_id = lobby_id
        self.listen_socket = listen_socket
        self.local_id = steam.steam_id
        self.access_code = access_code
        self.clock = clock
        self.preferred = preferred
        self.names: dict[int, str] = dict(names or {})
        known = dict(known or {})
        # The roster as this member last took it from a coordinator it trusts.
        # A member that creates the network starts with just itself.
        self.roster: roster.Roster = known if set(known) == {self.local_id} else {}
        # Whose roster this member takes: members of the roster it trusts.
        self._trusted: set[int] = set(known)
        self._known = known
        # The lobby owner, which is the coordinator (see above).
        self.coordinator_id = 0
        # Set when this member can't be admitted.
        self.refused = ""
        # Members that announced they leave the network for good.
        self.left: set[int] = set()
        # Members that dropped out of the lobby without leaving it (their
        # connection to Steam was lost), rather than going offline.
        self.lost: set[int] = set()
        # While suspended (this PC lost its connection to Steam) only the
        # connections are looked after; nothing is concluded about the others.
        self.suspended = False
        self.latency = LatencyTracker(clock)
        self.lobby = LobbySession(steam, lobby_id, listen_socket, log=log.info, clock=clock)
        # Checked IP packets from other members, waiting for the local adapter.
        self._packets: list[bytes] = []
        self.traffic = tunnel.TrafficStats()
        self._traffic_logged_at = clock()
        self._traffic_logged = ""
        self._waiting_since: dict[int, float] = {}
        self._failures: Counter[int] = Counter()
        self._auth_sent_to = 0
        self._auth_sent_at = 0.0
        self._handed_off_at: float | None = None
        self._ignored_roster = ""
        self._sync()

    @property
    def joined(self) -> bool:
        """Whether this member is in the roster, i.e. has its address."""
        return self.local_id in self.roster

    @property
    def is_coordinator(self) -> bool:
        return self.joined and self.coordinator_id == self.local_id

    @property
    def local_address(self) -> IPv4Address | None:
        """This member's virtual IP address, once it has one."""
        return self.roster.get(self.local_id)

    def process(self, callbacks: list[SteamCallback]) -> None:
        if not self.suspended:
            self._note_departures(callbacks)
        for steam_id, data in self.lobby.process(callbacks):
            packet = tunnel.packet_payload(data)
            if packet is not None:
                self._receive_packet(steam_id, packet)
                continue
            message = access.parse_message(data)
            if message is not None:
                self._message(steam_id, message)
        if self.suspended:
            return

        self._sync()
        if self.is_coordinator:
            self._coordinate()
        elif self.coordinator_id == self.local_id:
            self._hand_off()
        else:
            self._member_checks()
        self._ping()
        self._log_traffic()

    def close(self, linger: bool = False) -> None:
        self.lobby.close(linger)

    def suspend(self) -> None:
        """This PC lost its connection to Steam. Keep the roster and whatever
        connections survive, but draw no conclusions about the other members
        and start no connections until resume()."""
        if self.suspended:
            return
        self.suspended = True
        self.lobby.pause()
        self.latency.clear()

    def resume(self) -> None:
        """Back in the lobby after suspend(): follow it as it is now and
        reconnect to every member whose connection dropped meanwhile."""
        self.suspended = False
        self.lost.clear()
        self.lobby.resume()
        if not self.joined:
            # Whatever went wrong while the connection was down doesn't count.
            self.refused = ""
            self._auth_sent_to = 0

    def announce_leave(self) -> bool:
        """Tell the network this member leaves it for good (Leave Network), so
        that its address is freed. True once the coordinator knows, or there is
        nothing to tell; False while it can't be told yet."""
        if self.is_coordinator:
            # Nobody else can change the roster; do it before leaving the lobby.
            del self.roster[self.local_id]
            self._publish()
            self._send_leave()
            return True
        if not self.joined and self.coordinator_id == self.local_id:
            return True
        if self.coordinator_id in self.lobby.connected_peers():
            self._send_leave()
            return True
        return False

    @property
    def dropped_packets(self) -> int:
        return self.traffic.dropped_total

    def send_packet(self, packet: bytes) -> int:
        """Send an IP packet from the local adapter directly to the members it
        is for (tunnel.py): the member that owns its destination address, or,
        for broadcast and multicast, every member connected right now, once
        each. Returns the number of members it went to; 0 if it was dropped."""
        info = tunnel.outgoing(packet, self.local_address)
        if info is None:
            self.traffic.dropped("tx_unroutable")
            return 0
        if info.delivery is Delivery.UNICAST:
            owner = next(
                (
                    steam_id
                    for steam_id, address in self.roster.items()
                    if address == info.destination and steam_id != self.local_id
                ),
                None,
            )
            if owner is None:
                self.traffic.dropped("tx_unknown_destination")
                return 0
            recipients = [owner]
        else:
            # One copy for every member connected now: not this member, not
            # members that are offline, still joining or reconnecting. Each
            # member has one connection, so none gets two.
            recipients = sorted(
                steam_id
                for steam_id in self.lobby.connected_peers()
                if steam_id in self.roster and steam_id != self.local_id
            )
            if not recipients:
                self.traffic.dropped("tx_no_recipients")
                return 0
        message = tunnel.packet_message(packet)
        copies = 0
        for steam_id in recipients:
            try:
                self.lobby.send(steam_id, message, reliable=False)
            except SteamError:
                self.traffic.dropped("tx_send_failed")
            else:
                copies += 1
        if copies:
            self.traffic.sent(info.delivery, copies)
        return copies

    def take_packets(self) -> list[bytes]:
        """IP packets other members sent to this member since the last call.

        They are for this member's Windows only; nothing here ever sends them
        on to another member.
        """
        packets, self._packets = self._packets, []
        return packets

    def name_of(self, steam_id: int) -> str:
        if steam_id == self.local_id:
            return self.steam.persona_name
        # Steam knows the names of friends, and of other lobby members a
        # little after they entered; remember them for when they are offline.
        name = self.steam.friend_persona_name(steam_id)
        if name:
            self.names[steam_id] = name
        return self.names.get(steam_id) or display_id(steam_id)

    def members(self) -> list[MemberView]:
        """Everyone in the roster, online or not, and who is joining right now."""
        address = self.local_address
        views = [
            MemberView(
                self.local_id,
                self.name_of(self.local_id),
                is_you=True,
                online=True,
                status="Online" if self.joined else "Joining",
                tone="ok" if self.joined else "pending",
                address=str(address) if address is not None else "",
            )
        ]
        for steam_id, address in sorted(self.roster.items(), key=lambda item: item[1]):
            if steam_id == self.local_id:
                continue
            peer = self.lobby.peers.get(steam_id)
            latency = ""
            if peer is None and steam_id in self.lost:
                # Dropped out of the lobby without leaving it.
                status, tone = "Lost connection", "neutral"
            elif peer is None:
                status, tone = "Offline", "neutral"
            elif peer.connected:
                status, tone = "Online", "ok"
                latency = format_rtt(self.latency.rtt(steam_id))
            elif peer.was_connected:
                # Still in the lobby, just unreachable for now.
                status, tone = "Reconnecting...", "pending"
            else:
                status, tone = "Connecting...", "pending"
            views.append(
                MemberView(
                    steam_id,
                    self.name_of(steam_id),
                    is_you=False,
                    online=peer is not None,
                    status=status,
                    tone=tone,
                    address=str(address),
                    latency=latency,
                )
            )
        joining = [
            peer
            for peer in self.lobby.peers.values()
            if peer.steam_id not in self.roster and not (peer.ended and peer.retry_at is None)
        ]
        for peer in sorted(joining, key=lambda peer: peer.steam_id):
            views.append(
                MemberView(
                    peer.steam_id,
                    self.name_of(peer.steam_id),
                    is_you=False,
                    online=True,
                    status="Joining",
                    tone="pending",
                )
            )
        return views

    def status(self) -> tuple[str, str]:
        """This member's state in the network as (text, tone)."""
        if self.refused:
            return self.refused, "error"
        if not self.joined:
            return "Connecting to the network...", "pending"
        return "Online", "ok"

    def _message(self, steam_id: int, message: access.Message) -> None:
        if message.kind == "ping":
            try:
                self.lobby.send(steam_id, access.pong_message(message.sequence), reliable=False)
            except SteamError:
                pass
        elif message.kind == "pong":
            self.latency.pong(steam_id, message.sequence)
        elif message.kind == "leave":
            if steam_id in self.roster:
                log.info("%s left the network", steam_id)
                self.left.add(steam_id)
        elif message.kind == "auth":
            if self.is_coordinator:
                self._admit(steam_id, message)
        elif steam_id == self.coordinator_id and not self.is_coordinator:
            if message.kind == "accepted":
                # Only the coordinator can know the code; from now on this
                # member takes its roster.
                self._trusted.add(steam_id)
                self.access_code = message.code
                log.info("Admitted to the network by %s", steam_id)
            elif message.kind == "denied" and not self.joined:
                if self.access_code:
                    self.refused = "Incorrect access code"
                else:
                    self.refused = "Ask a member of the network for an invite or the access code"

    def _sync(self) -> None:
        """Follow Steam's lobby owner and take the roster it wrote, if trusted."""
        try:
            owner = self.steam.lobby_owner(self.lobby_id)
        except SteamError:
            # Steam's copy of the lobby is mid-update; nothing changed yet.
            return
        previous = self.coordinator_id
        if owner != previous:
            self.coordinator_id = owner
            log.info("The network is now coordinated by %s", owner or "nobody")
        if owner == self.local_id and self.joined:
            if previous != owner and previous in self._trusted:
                # Just took over: first pick up what the previous coordinator
                # last wrote, in case it arrived together with the change.
                self._take_roster(previous)
            return
        self._take_roster(owner)
        if (
            owner == self.local_id
            and not self.joined
            and self.local_id in self._known
            and not self.steam.lobby_data(self.lobby_id, roster.ROSTER_KEY)
        ):
            # Back as the owner of a lobby that has no roster: restore ours.
            self.roster = dict(self._known)

    def _take_roster(self, writer: int) -> None:
        if writer == 0 or writer not in self._trusted:
            return
        text = self.steam.lobby_data(self.lobby_id, roster.ROSTER_KEY)
        if not text or text == roster.encode(self.roster):
            return
        members = roster.decode(text)
        own = self.local_address
        fits = members is not None and (own is None or members.get(self.local_id) == own)
        if not fits:
            if text != self._ignored_roster:
                self._ignored_roster = text
                log.warning("Ignored a roster that doesn't fit this network")
            return
        # A member that is new in the roster was admitted again after leaving.
        self.left -= set(members) - set(self.roster)
        self.left &= set(members)
        self.roster = members
        self._trusted |= set(members)

    def _publish(self) -> None:
        text = roster.encode(self.roster)
        if self.steam.lobby_data(self.lobby_id, roster.ROSTER_KEY) == text:
            return
        try:
            self.steam.set_lobby_data(self.lobby_id, roster.ROSTER_KEY, text)
        except SteamError as exc:
            # Ownership just moved on; the new coordinator takes over.
            log.debug("Could not update the roster: %s", exc)

    def _coordinate(self) -> None:
        for steam_id in self.left & set(self.roster):
            if steam_id != self.local_id:
                del self.roster[steam_id]
        self._publish()

        now = self.clock()
        connected = set(self.lobby.connected_peers())
        for steam_id in connected - set(self.roster):
            since = self._waiting_since.setdefault(steam_id, now)
            if now - since > AUTH_TIMEOUT:
                self._deny(steam_id, "no access code")
        for steam_id in set(self._waiting_since) - connected:
            del self._waiting_since[steam_id]

    def _admit(self, steam_id: int, message: access.Message) -> None:
        if steam_id in self.roster:
            # A member coming back; its SteamID is all it needs.
            self._accept(steam_id)
            return
        if self._failures[steam_id] >= MAX_FAILURES:
            self._deny(steam_id, "too many access attempts")
            return
        if not self.access_code or not access.access_code_matches(message.code, self.access_code):
            self._failures[steam_id] += 1
            self._deny(steam_id, "wrong access code")
            return
        try:
            address = roster.assign(self.roster, steam_id, message.address)
        except ValueError:
            self._deny(steam_id, "the network is full")
            return
        self.left.discard(steam_id)
        self._trusted.add(steam_id)
        self._publish()
        log.info("Admitted %s as %s", steam_id, address)
        self._accept(steam_id)

    def _accept(self, steam_id: int) -> None:
        self._waiting_since.pop(steam_id, None)
        try:
            self.lobby.send(steam_id, access.accepted_message(self.access_code))
        except SteamError as exc:
            log.debug("Could not answer %s: %s", steam_id, exc)

    def _deny(self, steam_id: int, reason: str) -> None:
        log.info("Denied %s: %s", steam_id, reason)
        self._waiting_since.pop(steam_id, None)
        try:
            self.lobby.send(steam_id, access.DENIED)
        except SteamError:
            pass
        # Linger so the reply still reaches the member after the close.
        self.lobby.disconnect(steam_id, f"access denied: {reason}", linger=True)

    def _hand_off(self) -> None:
        """This member owns the lobby without being a member of the network:
        pass ownership to a member that is."""
        now = self.clock()
        if self._handed_off_at is not None and now - self._handed_off_at < HAND_OFF_INTERVAL:
            return
        members = roster.decode(self.steam.lobby_data(self.lobby_id, roster.ROSTER_KEY)) or {}
        online = set(members) & set(self.lobby.peers)
        if not online:
            self.refused = NO_MEMBERS_ONLINE
            return
        self._handed_off_at = now
        try:
            self.steam.set_lobby_owner(self.lobby_id, min(online))
        except SteamError as exc:
            log.warning("Could not hand the network on: %s", exc)

    def _member_checks(self) -> None:
        if self.refused or self.joined:
            return
        coordinator = self.coordinator_id
        if coordinator and self._auth_sent_to != coordinator:
            if coordinator in self.lobby.connected_peers():
                self.lobby.send(coordinator, access.auth_message(self.access_code, self.preferred))
                self._auth_sent_to = coordinator
                self._auth_sent_at = self.clock()
        elif self._auth_sent_to and self.clock() - self._auth_sent_at > AUTH_TIMEOUT:
            self.refused = "The network did not answer"

    def _note_departures(self, callbacks: list[SteamCallback]) -> None:
        for callback in callbacks:
            event = decode_lobby_event(callback)
            if not isinstance(event, LobbyMemberUpdate) or event.lobby_id != self.lobby_id:
                continue
            if event.user_id == self.local_id:
                continue
            if event.state & ChatMemberStateChange.DISCONNECTED:
                self.lost.add(event.user_id)
                if event.user_id in self.roster:
                    log.info("Peer lost its connection to Steam: %s", event.user_id)
            else:
                self.lost.discard(event.user_id)
                if event.state & ChatMemberStateChange.LEFT and event.user_id in self.roster:
                    log.info("Peer went offline: %s", event.user_id)

    def _ping(self) -> None:
        """Measure the round trip to every connected member (latency.py)."""
        connected = {
            peer.steam_id: peer.connection
            for peer in self.lobby.peers.values()
            if peer.connected and peer.steam_id in self.roster
        }
        self.latency.track(connected)
        for steam_id, sequence in self.latency.due():
            try:
                # Unreliable, like the packets: a lost PING is just lost,
                # instead of being resent and measuring the resend.
                self.lobby.send(steam_id, access.ping_message(sequence), reliable=False)
            except SteamError:
                pass

    def _send_leave(self) -> None:
        for steam_id in self.lobby.connected_peers():
            try:
                self.lobby.send(steam_id, access.LEAVE)
            except SteamError:
                pass

    def _receive_packet(self, steam_id: int, packet: bytes) -> None:
        sender_address = self.roster.get(steam_id) if steam_id != self.local_id else None
        delivery = tunnel.accepts_packet(packet, sender_address, self.local_address)
        if delivery is None:
            self.traffic.dropped("rx_rejected")
            return
        self._packets.append(packet)
        self.traffic.received(delivery)

    def _log_traffic(self) -> None:
        now = self.clock()
        if now - self._traffic_logged_at < TRAFFIC_LOG_INTERVAL:
            return
        self._traffic_logged_at = now
        summary = self.traffic.summary()
        if summary != self._traffic_logged:
            self._traffic_logged = summary
            log.debug("Tunnel traffic: %s", summary)
