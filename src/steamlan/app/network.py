"""A SteamVirtualLAN network: lobby members connected by LobbySession, admitted by the host.

The host also gives every admitted member a virtual IP address and tells all
members the whole SteamID -> address mapping. IP packets then go straight to
the member that owns their destination address.
"""

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address

from steamlan.app import access, tunnel
from steamlan.app.addresses import AddressPool
from steamlan.steam import LobbySession, SteamCallback, SteamClient, SteamError

log = logging.getLogger(__name__)

# How long a connected member has to present its access code.
AUTH_TIMEOUT = 20.0
# Wrong codes after which the host stops listening to a SteamID.
MAX_FAILURES = 5


@dataclass(frozen=True)
class MemberView:
    steam_id: int
    name: str
    is_you: bool
    is_host: bool
    status: str
    tone: str  # "ok", "pending", "error" or "neutral"
    address: str = ""  # virtual IP address, once the host assigned one


def display_id(steam_id: int) -> str:
    return f"Steam user {str(steam_id)[-4:]}"


class NetworkSession:
    def __init__(
        self,
        steam: SteamClient,
        lobby_id: int,
        listen_socket: int,
        host_id: int,
        access_code: str = "",
        clock: Callable[[], float] = time.monotonic,
    ):
        """access_code: the host's own code, or the code a joining member presents."""
        self.steam = steam
        self.lobby_id = lobby_id
        self.listen_socket = listen_socket
        self.local_id = steam.steam_id
        self.host_id = host_id
        self.is_host = host_id == self.local_id
        self.access_code = access_code
        self.clock = clock
        # Host: members that proved access. Member: members the host approved.
        self.approved: set[int] = set()
        # Set on a member when the host refused it or stopped answering.
        self.refused = ""
        self.lobby = LobbySession(steam, lobby_id, listen_socket, log=log.info)
        # SteamID -> virtual IP address. The host assigns them; members only
        # learn them from the host and never pick their own.
        self._pool = AddressPool(self.local_id) if self.is_host else None
        self.addresses: dict[int, IPv4Address] = dict(self._pool.assigned) if self._pool else {}
        # Checked IP packets from other members, waiting for the local adapter.
        self._packets: list[bytes] = []
        self.dropped_packets = 0
        self._waiting_since: dict[int, float] = {}
        self._failures: Counter[int] = Counter()
        self._auth_sent_at: float | None = None

    @property
    def host_present(self) -> bool:
        return self.is_host or self.host_id in self.lobby.peers

    @property
    def joined(self) -> bool:
        """A member counts as joined once the host has approved it."""
        return self.is_host or self.host_id in self.approved

    @property
    def local_address(self) -> IPv4Address | None:
        """This member's virtual IP address, once the host assigned it."""
        return self.addresses.get(self.local_id)

    def process(self, callbacks: list[SteamCallback]) -> None:
        for steam_id, data in self.lobby.process(callbacks):
            packet = tunnel.packet_payload(data)
            if packet is not None:
                self._receive_packet(steam_id, packet)
                continue
            message = access.parse_message(data)
            if message is None:
                continue
            if self.is_host:
                self._host_message(steam_id, message)
            elif steam_id == self.host_id:
                self._member_message(message)

        if self.is_host:
            self._host_checks()
        else:
            self._member_checks()

    def close(self) -> None:
        self.lobby.close()

    def send_packet(self, packet: bytes) -> bool:
        """Send an IP packet from the local adapter to the member that owns its
        destination address. False if it was dropped."""
        owners = {
            address: steam_id
            for steam_id, address in self.addresses.items()
            if steam_id in self.approved
        }
        steam_id = tunnel.destination_member(packet, self.local_address, owners)
        if steam_id is None:
            self.dropped_packets += 1
            return False
        try:
            self.lobby.send(steam_id, tunnel.packet_message(packet), reliable=False)
        except SteamError as exc:
            log.debug("Dropped a packet for %s: %s", steam_id, exc)
            self.dropped_packets += 1
            return False
        return True

    def take_packets(self) -> list[bytes]:
        """IP packets other members sent to this member since the last call."""
        packets, self._packets = self._packets, []
        return packets

    def _receive_packet(self, steam_id: int, packet: bytes) -> None:
        sender_address = self.addresses.get(steam_id) if steam_id in self.approved else None
        if tunnel.accepts_packet(packet, sender_address, self.local_address):
            self._packets.append(packet)
        else:
            self.dropped_packets += 1

    def members(self) -> list[MemberView]:
        if self.is_host:
            own_status, own_tone = "Hosting", "ok"
        elif self.joined:
            own_status, own_tone = "Connected", "ok"
        else:
            own_status, own_tone = "Joining", "pending"
        views = [
            MemberView(
                self.local_id,
                self.steam.persona_name,
                is_you=True,
                is_host=self.is_host,
                status=own_status,
                tone=own_tone,
                address=self._address_text(self.local_id),
            )
        ]
        for peer in self.lobby.peers.values():
            status, tone = self._peer_status(peer)
            # Steam sends names of new lobby members a little later.
            name = self.steam.friend_persona_name(peer.steam_id) or display_id(peer.steam_id)
            views.append(
                MemberView(
                    peer.steam_id,
                    name,
                    is_you=False,
                    is_host=peer.steam_id == self.host_id,
                    status=status,
                    tone=tone,
                    address=self._address_text(peer.steam_id),
                )
            )
        views.sort(key=lambda view: (not view.is_host, not view.is_you, view.name.casefold()))
        return views

    def status(self) -> tuple[str, str]:
        """Overall network status as (text, tone)."""
        if not self.is_host:
            if self.refused:
                return self.refused, "error"
            if not self.host_present:
                return "Host left the network", "error"
            host = self.lobby.peers[self.host_id]
            if host.ended:
                return "Connection lost", "error"
            if self.joined:
                return "Connected", "ok"
            return "Connecting to host", "pending"

        if not self.lobby.peers:
            return "Waiting for peers", "neutral"
        connected = [steam_id for steam_id in self.approved if steam_id in self.lobby.peers]
        if connected:
            return f"Connected to {len(connected)} of {len(self.lobby.peers)}", "ok"
        if all(peer.ended for peer in self.lobby.peers.values()):
            return "Connection lost", "error"
        return "Connecting", "pending"

    def _address_text(self, steam_id: int) -> str:
        address = self.addresses.get(steam_id)
        return str(address) if address is not None else ""

    def _peer_status(self, peer) -> tuple[str, str]:
        if peer.ended:
            if self.is_host and peer.steam_id not in self.approved and "access" in peer.ended:
                return "Access denied", "error"
            return "Connection lost", "error"
        if not peer.connected:
            return "Connecting", "pending"
        if peer.steam_id in self.approved or (peer.steam_id == self.host_id and self.joined):
            return "Connected", "ok"
        return ("Checking access code" if self.is_host else "Waiting for host"), "pending"

    def _host_message(self, steam_id: int, message: access.Message) -> None:
        if message.kind != "auth" or steam_id in self.approved:
            return
        if self._failures[steam_id] >= MAX_FAILURES:
            self._deny(steam_id, "too many access attempts")
        elif access.access_code_matches(message.code, self.access_code):
            self._approve(steam_id)
        else:
            self._failures[steam_id] += 1
            self._deny(steam_id, "wrong access code")

    def _approve(self, steam_id: int) -> None:
        try:
            address = self._pool.assign(steam_id)
        except ValueError:
            self._deny(steam_id, "the network is full")
            return
        self.addresses = dict(self._pool.assigned)
        self.approved.add(steam_id)
        self._waiting_since.pop(steam_id, None)
        self.lobby.send(steam_id, access.ACCEPTED)
        log.info("Approved %s as %s", steam_id, address)
        self._send_members()

    def _deny(self, steam_id: int, reason: str) -> None:
        log.info("Denied %s: %s", steam_id, reason)
        self._waiting_since.pop(steam_id, None)
        try:
            self.lobby.send(steam_id, access.DENIED)
        except SteamError:
            pass
        # Linger so the reply still reaches the member after the close.
        self.lobby.disconnect(steam_id, f"access denied: {reason}", linger=True)

    def _host_checks(self) -> None:
        gone = self.approved - set(self.lobby.peers)
        if gone:
            self.approved -= gone
            for steam_id in gone:
                self._pool.release(steam_id)
            self.addresses = dict(self._pool.assigned)
            self._send_members()

        now = self.clock()
        connected = set(self.lobby.connected_peers())
        for steam_id in connected - self.approved:
            since = self._waiting_since.setdefault(steam_id, now)
            if now - since > AUTH_TIMEOUT:
                self._deny(steam_id, "no access code")
        for steam_id in set(self._waiting_since) - connected:
            del self._waiting_since[steam_id]

    def _send_members(self) -> None:
        """Tell every admitted member who is admitted, and everyone's address."""
        message = access.members_message(self.addresses)
        for steam_id in self.approved & set(self.lobby.connected_peers()):
            self.lobby.send(steam_id, message)

    def _member_message(self, message: access.Message) -> None:
        if message.kind == "accepted":
            self.approved.add(self.host_id)
            log.info("The host approved this member")
        elif message.kind == "denied":
            if self.access_code:
                self.refused = "Incorrect access code"
            else:
                self.refused = "Ask the host for an invite or the access code"
        elif message.kind == "members":
            addresses = dict(message.addresses)
            own = addresses.get(self.local_id)
            if self.host_id not in addresses or (
                self.local_address is not None and own != self.local_address
            ):
                log.warning("Ignored a member list from the host that doesn't fit this network")
                return
            self.addresses = addresses
            self.approved = (set(addresses) | {self.host_id}) - {self.local_id}

    def _member_checks(self) -> None:
        if self.refused or self.joined:
            return
        if self._auth_sent_at is None:
            if self.host_id in self.lobby.connected_peers():
                self.lobby.send(self.host_id, access.auth_message(self.access_code))
                self._auth_sent_at = self.clock()
        elif self.clock() - self._auth_sent_at > AUTH_TIMEOUT:
            self.refused = "The host did not answer"
