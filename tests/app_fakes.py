"""A fake SteamClient for the app tests, with helpers for the callbacks it returns."""

from ipaddress import IPv4Address

from steamlan.steam import ChatMemberStateChange, ConnectionState, SteamCallback, SteamError
from steamlan.steam.native import (
    CONNECTION_STATUS_CHANGED,
    GAME_LOBBY_JOIN_REQUESTED,
    GAME_RICH_PRESENCE_JOIN_REQUESTED,
    LOBBY_CHAT_UPDATE,
    LOBBY_CREATED,
    LOBBY_ENTER,
    ChatRoomEnterResponse,
    EResult,
    GameLobbyJoinRequested,
    GameRichPresenceJoinRequested,
    LobbyChatUpdate,
    LobbyCreated,
    LobbyEnter,
    SteamNetConnectionStatusChangedCallback,
)

HOST = 76561197960265729
GUEST = 76561197960265730
OTHER = 76561197960265731
STRANGER = 76561197960265799
# SteamIDs of chat accounts, like real lobby IDs.
LOBBY = (1 << 56) | (8 << 52) | (0x60000 << 32) | 1234
OTHER_LOBBY = LOBBY + 1
LISTEN_SOCKET = 0x55
CREATE_CALL = 900
JOIN_CALL = 901


def ipv4_packet(source, destination, payload=b"ping", protocol=1):
    """A minimal IPv4 packet; the header checksum is not needed by anything here."""
    header = bytearray(20)
    header[0] = 0x45
    header[2:4] = (20 + len(payload)).to_bytes(2, "big")
    header[8] = 128
    header[9] = protocol
    header[12:16] = IPv4Address(source).packed
    header[16:20] = IPv4Address(destination).packed
    return bytes(header) + payload


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def udp_packet(source, destination, source_port, destination_port, data=b"discover", ttl=1):
    """A complete IPv4/UDP datagram with valid checksums, as Windows would send
    it for a game's LAN discovery."""
    source, destination = IPv4Address(source), IPv4Address(destination)
    length = 8 + len(data)
    udp = bytearray(
        source_port.to_bytes(2, "big")
        + destination_port.to_bytes(2, "big")
        + length.to_bytes(2, "big")
        + b"\0\0"
        + data
    )
    pseudo = source.packed + destination.packed + b"\0\x11" + length.to_bytes(2, "big")
    udp[6:8] = (_checksum(pseudo + bytes(udp)) or 0xFFFF).to_bytes(2, "big")
    header = bytearray(20)
    header[0] = 0x45
    header[2:4] = (20 + length).to_bytes(2, "big")
    header[4:6] = b"\x4a\x21"  # identification
    header[8] = ttl
    header[9] = 17
    header[12:16] = source.packed
    header[16:20] = destination.packed
    header[10:12] = _checksum(bytes(header)).to_bytes(2, "big")
    return bytes(header) + bytes(udp)


def udp_ports(packet):
    """(source port, destination port, data) of an IPv4/UDP packet with a 20-byte header."""
    return (
        int.from_bytes(packet[20:22], "big"),
        int.from_bytes(packet[22:24], "big"),
        packet[28:],
    )


def status(connection, state, remote, listen_socket=0):
    changed = SteamNetConnectionStatusChangedCallback()
    changed.m_hConn = connection
    info = changed.m_info
    info.m_identityRemote.m_eType = 16
    info.m_identityRemote.m_cbSize = 8
    info.m_identityRemote.m_data[:8] = remote.to_bytes(8, "little")
    info.m_hListenSocket = listen_socket
    info.m_eState = state
    return SteamCallback(CONNECTION_STATUS_CHANGED, bytes(changed))


def incoming(connection, remote):
    return status(connection, ConnectionState.CONNECTING, remote, LISTEN_SOCKET)


def member_update(user_id, state=ChatMemberStateChange.ENTERED, lobby_id=LOBBY):
    return SteamCallback(
        LOBBY_CHAT_UPDATE, bytes(LobbyChatUpdate(lobby_id, user_id, user_id, state))
    )


def lobby_created(result=EResult.OK, lobby_id=LOBBY, api_call=CREATE_CALL):
    return SteamCallback(LOBBY_CREATED, bytes(LobbyCreated(result, lobby_id)), api_call)


def lobby_entered(response=ChatRoomEnterResponse.SUCCESS, lobby_id=LOBBY, api_call=JOIN_CALL):
    return SteamCallback(LOBBY_ENTER, bytes(LobbyEnter(lobby_id, 0, False, response)), api_call)


def join_requested(lobby_id=LOBBY, friend_id=HOST):
    return SteamCallback(
        GAME_LOBBY_JOIN_REQUESTED, bytes(GameLobbyJoinRequested(lobby_id, friend_id))
    )


def invite_accepted(connect, friend_id=HOST):
    return SteamCallback(
        GAME_RICH_PRESENCE_JOIN_REQUESTED,
        bytes(GameRichPresenceJoinRequested(friend_id, connect.encode())),
    )


class FakeSteam:
    def __init__(self, steam_id=HOST, members=None, names=None):
        self.steam_id = steam_id
        self.persona_name = "Me"
        self.members = list(members if members is not None else [steam_id])
        self.names = dict(names or {})
        self.frames = []
        self.inbox = {}
        self.lobby_metadata = {}
        self.owner = HOST
        self.next_connection = 100
        self.calls = []
        self.sent = []
        # Messages sent unreliably: the IP packets.
        self.unreliable = []
        self.closed = []
        self.fail = set()
        self.running = True
        self.overlay_enabled = True
        self.needs_present = False
        self.logged_on = True

    def _record(self, name, *args):
        if name in self.fail:
            raise SteamError(f"{name} failed")
        self.calls.append((name, *args))

    def run_callbacks(self):
        return self.frames.pop(0) if self.frames else []

    def request_create_lobby(self, max_members):
        self._record("request_create_lobby", max_members)
        return CREATE_CALL

    def request_join_lobby(self, lobby_id):
        self._record("request_join_lobby", lobby_id)
        return JOIN_CALL

    def set_lobby_data(self, lobby_id, key, value):
        self._record("set_lobby_data", lobby_id, key, value)
        if self.owner != self.steam_id:
            raise SteamError(f"SetLobbyData failed for {key!r}")
        self.lobby_metadata[key] = value

    def lobby_data(self, lobby_id, key):
        return self.lobby_metadata.get(key, "")

    def lobby_owner(self, lobby_id):
        return self.owner

    def set_lobby_owner(self, lobby_id, new_owner):
        self._record("set_lobby_owner", lobby_id, new_owner)
        self.owner = new_owner

    def lobby_members(self, lobby_id):
        return list(self.members)

    def leave_lobby(self, lobby_id):
        self._record("leave_lobby", lobby_id)

    def invite_to_lobby(self, lobby_id, friend_id):
        self._record("invite_to_lobby", lobby_id, friend_id)

    def open_invite_dialog(self, connect_string):
        self._record("open_invite_dialog", connect_string)

    def overlay_needs_present(self):
        return self.needs_present

    def friend_persona_name(self, steam_id):
        return self.names.get(steam_id, "")

    def create_listen_socket(self, virtual_port=0):
        self._record("create_listen_socket", virtual_port)
        return LISTEN_SOCKET

    def close_listen_socket(self, listen_socket):
        self._record("close_listen_socket", listen_socket)
        return True

    def connect_p2p(self, remote_steam_id, virtual_port=0):
        self.next_connection += 1
        self._record("connect_p2p", remote_steam_id, self.next_connection)
        return self.next_connection

    def accept_connection(self, connection):
        self._record("accept_connection", connection)

    def close_connection(self, connection, debug="", linger=False):
        self.calls.append(("close_connection", connection))
        self.closed.append((connection, linger))
        return True

    def send_message(self, connection, data, reliable=True):
        (self.sent if reliable else self.unreliable).append((connection, data))

    def receive_messages(self, connection):
        return self.inbox.pop(connection, [])

    def close(self):
        self._record("close")
        self.running = False

    def called(self, name):
        return [call[1:] for call in self.calls if call[0] == name]

    @property
    def packets(self):
        """The IP packets sent, without the PINGs and PONGs that share their channel."""
        return [(c, data) for c, data in self.unreliable if data[:1] == b"\x00"]


class FakeHelper:
    """Stands in for AdapterHelper: no UAC prompt, helper process or adapter.

    Tests move it along with become_ready() and fail(). Steps that matter for
    ordering are recorded in steam.calls, next to the Steam calls.
    """

    def __init__(self, steam):
        from steamlan.adapter.launcher import State

        self.State = State
        self.steam = steam
        self.state = State.IDLE
        self.progress = ""
        self.error = ""
        self.address = None
        self.requested = []
        self.from_windows = []
        self.to_windows = []

    @property
    def ready(self):
        return self.state is self.State.READY

    def start(self):
        self.steam.calls.append(("helper_start",))
        self.state = self.State.STARTING
        self.progress = "Waiting for Administrator permission..."

    def become_ready(self):
        self.state = self.State.READY
        self.progress = ""

    def fail(self, error):
        self.state = self.State.FAILED
        self.error = error

    def poll(self):
        packets, self.from_windows = self.from_windows, []
        return packets

    def set_address(self, address):
        if self.ready and address not in self.requested:
            self.requested.append(address)
            # The real helper confirms a little later, through poll().
            self.address = address

    def send_packet(self, packet):
        if self.ready and self.address is not None:
            self.to_windows.append(packet)

    def close(self):
        self.steam.calls.append(("helper_close",))
        self.state = self.State.STOPPED
