"""Keep one SteamNetworkingSockets connection to every other member of a lobby."""

from collections.abc import Callable
from dataclasses import dataclass

from steamlan.steam.client import SteamCallback, SteamClient, SteamError
from steamlan.steam.lobby import LobbyMemberUpdate, decode_lobby_event
from steamlan.steam.native import ConnectionState
from steamlan.steam.networking import ConnectionStatusChange, decode_networking_event


def initiates(local_id: int, remote_id: int) -> bool:
    """Of two members, the one with the lower SteamID connects and the other accepts."""
    if local_id == remote_id:
        raise ValueError("a member does not connect to itself")
    return local_id < remote_id


@dataclass
class Peer:
    steam_id: int
    initiator: bool
    connection: int = 0
    connected: bool = False
    # Why the last connection ended. There is no automatic reconnect.
    ended: str = ""


class LobbySession:
    def __init__(
        self,
        steam: SteamClient,
        lobby_id: int,
        listen_socket: int,
        log: Callable[[str], None] = lambda message: None,
    ):
        self.steam = steam
        self.lobby_id = lobby_id
        self.listen_socket = listen_socket
        self.local_id = steam.steam_id
        self.log = log
        self.peers: dict[int, Peer] = {}
        self.update_members()

    def connected_peers(self) -> list[int]:
        return [peer.steam_id for peer in self.peers.values() if peer.connected]

    def update_members(self) -> None:
        """Make the peers match Steam's current lobby member list."""
        members = set(self.steam.lobby_members(self.lobby_id)) - {self.local_id}
        for steam_id in sorted(members - self.peers.keys()):
            self.peers[steam_id] = Peer(steam_id, initiates(self.local_id, steam_id))
            self.log(f"Peer joined: {steam_id}")
        for steam_id in sorted(self.peers.keys() - members):
            self._close(self.peers.pop(steam_id), "left the lobby")
            self.log(f"Peer left: {steam_id}")

    def poll(self) -> list[tuple[int, bytes]]:
        """Handle pending callbacks and return (steam_id, data) for received messages."""
        return self.process(self.steam.run_callbacks())

    def process(self, callbacks: list[SteamCallback]) -> list[tuple[int, bytes]]:
        """Like poll(), for callbacks the caller already took from run_callbacks()."""
        for callback in callbacks:
            lobby_event = decode_lobby_event(callback)
            if isinstance(lobby_event, LobbyMemberUpdate):
                if lobby_event.lobby_id == self.lobby_id:
                    self.update_members()
                continue
            event = decode_networking_event(callback)
            if isinstance(event, ConnectionStatusChange):
                self._connection_changed(event)

        self._connect()
        return self._receive()

    def send(self, steam_id: int, data: bytes, reliable: bool = True) -> None:
        peer = self.peers.get(steam_id)
        if peer is None or not peer.connected:
            raise SteamError(f"not connected to {steam_id}")
        self.steam.send_message(peer.connection, data, reliable=reliable)

    def disconnect(self, steam_id: int, reason: str, linger: bool = False) -> None:
        """Close the connection to a peer and don't connect to it again."""
        peer = self.peers.get(steam_id)
        if peer is not None:
            self._close(peer, reason, linger)

    def close(self) -> None:
        for peer in self.peers.values():
            self._close(peer, "session closed")

    def _connect(self) -> None:
        for peer in self.peers.values():
            if peer.initiator and not peer.connection and not peer.ended:
                self.log(f"Connecting to {peer.steam_id}...")
                peer.connection = self.steam.connect_p2p(peer.steam_id, 0)

    def _connection_changed(self, event: ConnectionStatusChange) -> None:
        peer = next((p for p in self.peers.values() if p.connection == event.connection), None)
        if peer is None:
            if event.incoming and event.listen_socket == self.listen_socket:
                self._accept(event)
            elif event.ended:
                self.steam.close_connection(event.connection)
            return

        if event.remote_steam_id is not None and event.remote_steam_id != peer.steam_id:
            self._close(peer, f"connection identified as {event.remote_steam_id}")
        elif event.state is ConnectionState.CONNECTED and not peer.connected:
            peer.connected = True
            self.log(f"Connected to {peer.steam_id}")
        elif event.ended:
            self._close(peer, f"{event.state.name}, reason {event.end_reason}: {event.end_debug}")

    def _accept(self, event: ConnectionStatusChange) -> None:
        steam_id = event.remote_steam_id
        if steam_id is not None and steam_id not in self.peers:
            # A new member can connect before its LobbyChatUpdate is handled.
            self.update_members()

        peer = self.peers.get(steam_id) if steam_id is not None else None
        if peer is None:
            reason = "not a lobby member"
        elif peer.initiator:
            reason = "this side connects to that peer"
        elif peer.connection:
            reason = "already connected"
        else:
            reason = ""
            try:
                self.steam.accept_connection(event.connection)
            except SteamError as exc:
                reason = str(exc)

        if reason:
            self.steam.close_connection(event.connection, reason)
            self.log(f"Rejected connection from {steam_id}: {reason}")
            return
        peer.connection = event.connection
        peer.ended = ""
        self.log(f"Accepted connection from {steam_id}")

    def _receive(self) -> list[tuple[int, bytes]]:
        received = []
        for peer in self.peers.values():
            if peer.connected:
                messages = self.steam.receive_messages(peer.connection)
                received += [(peer.steam_id, data) for data in messages]
        return received

    def _close(self, peer: Peer, reason: str, linger: bool = False) -> None:
        if peer.connection:
            self.steam.close_connection(peer.connection, reason, linger)
            self.log(f"Connection to {peer.steam_id} closed: {reason}")
        peer.connection = 0
        peer.connected = False
        peer.ended = reason
