"""An in-memory Steam for tests with several members: lobbies and P2P connections.

It follows what the Steamworks SDK (1.65) documents for lobbies:

- the creator is the first owner; there is always exactly one owner while the
  lobby has members, and when the owner leaves or disconnects Steam picks
  another member by itself. Which one is not documented, so the World lets a
  test choose (`pick_owner`);
- only the owner can SetLobbyData and SetLobbyOwner;
- a lobby is destroyed when its last member leaves, after which JoinLobby
  answers k_EChatRoomEnterResponseDoesntExist.

Connections deliver messages at once; callbacks queue until run_callbacks().
"""

from dataclasses import dataclass, field

from app_fakes import lobby_created, lobby_entered, member_update, status

from steamlan.steam import ChatMemberStateChange, ConnectionState, SteamError
from steamlan.steam.native import ChatRoomEnterResponse

FIRST_LOBBY = (1 << 56) | (8 << 52) | (0x60000 << 32) | 5000


@dataclass
class Lobby:
    lobby_id: int
    members: list[int]
    owner: int
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Connection:
    local: int
    remote: int
    handle: int
    remote_handle: int = 0
    open: bool = True
    connected: bool = False


class World:
    def __init__(self, pick_owner=None):
        self.lobbies: dict[int, Lobby] = {}
        self.clients: dict[int, SimSteam] = {}
        self.next_lobby = FIRST_LOBBY
        self.next_handle = 1000
        self.next_call = 500
        # Which remaining member Steam makes the owner; by default the one that
        # has been in the lobby longest.
        self.pick_owner = pick_owner or (lambda lobby: lobby.members[0])

    def handle(self) -> int:
        self.next_handle += 1
        return self.next_handle

    def call(self) -> int:
        self.next_call += 1
        return self.next_call

    def client(self, steam_id: int, name: str = "") -> "SimSteam":
        steam = SimSteam(self, steam_id, name or f"Player {str(steam_id)[-2:]}")
        self.clients[steam_id] = steam
        return steam

    def remove_member(self, lobby_id: int, steam_id: int, change: ChatMemberStateChange):
        lobby = self.lobbies.get(lobby_id)
        if lobby is None or steam_id not in lobby.members:
            return
        lobby.members.remove(steam_id)
        if not lobby.members:
            # Steam destroys a lobby once all of its members have left.
            del self.lobbies[lobby_id]
            return
        if lobby.owner == steam_id:
            lobby.owner = self.pick_owner(lobby)
        for member in lobby.members:
            self.clients[member].queue.append(member_update(steam_id, change, lobby_id))


class SimSteam:
    """Implements the SteamClient methods the app uses, over a World."""

    def __init__(self, world: World, steam_id: int, name: str):
        self.world = world
        self.steam_id = steam_id
        self.persona_name = name
        self.running = True
        self.queue = []
        self.connections: dict[int, Connection] = {}
        self.inbox: dict[int, list[bytes]] = {}
        self.listen_sockets: set[int] = set()
        self.lobbies: set[int] = set()
        self.calls = []
        self.overlay_enabled = True

    # Callbacks

    def run_callbacks(self):
        if not self.running:
            raise SteamError("Steam API is not running")
        callbacks, self.queue = self.queue, []
        return callbacks

    # Lobbies

    def request_create_lobby(self, max_members):
        world = self.world
        world.next_lobby += 1
        lobby = Lobby(world.next_lobby, [self.steam_id], self.steam_id)
        world.lobbies[lobby.lobby_id] = lobby
        self.lobbies.add(lobby.lobby_id)
        api_call = world.call()
        self.queue.append(lobby_created(lobby_id=lobby.lobby_id, api_call=api_call))
        return api_call

    def request_join_lobby(self, lobby_id):
        api_call = self.world.call()
        lobby = self.world.lobbies.get(lobby_id)
        if lobby is None:
            self.queue.append(lobby_entered(ChatRoomEnterResponse.DOESNT_EXIST, lobby_id, api_call))
            return api_call
        for member in lobby.members:
            self.world.clients[member].queue.append(member_update(self.steam_id, lobby_id=lobby_id))
        lobby.members.append(self.steam_id)
        self.lobbies.add(lobby_id)
        self.queue.append(lobby_entered(lobby_id=lobby_id, api_call=api_call))
        return api_call

    def leave_lobby(self, lobby_id):
        self.calls.append(("leave_lobby", lobby_id))
        self.lobbies.discard(lobby_id)
        self.world.remove_member(lobby_id, self.steam_id, ChatMemberStateChange.LEFT)

    def _lobby(self, lobby_id) -> Lobby:
        lobby = self.world.lobbies.get(lobby_id)
        if lobby is None or self.steam_id not in lobby.members:
            raise SteamError("not in that lobby")
        return lobby

    def lobby_owner(self, lobby_id):
        try:
            return self._lobby(lobby_id).owner
        except SteamError:
            raise SteamError("Steam returned no lobby owner") from None

    def lobby_members(self, lobby_id):
        lobby = self.world.lobbies.get(lobby_id)
        if lobby is None or self.steam_id not in lobby.members:
            return []
        return list(lobby.members)

    def lobby_data(self, lobby_id, key):
        lobby = self.world.lobbies.get(lobby_id)
        return lobby.data.get(key, "") if lobby is not None else ""

    def set_lobby_data(self, lobby_id, key, value):
        lobby = self._lobby(lobby_id)
        if lobby.owner != self.steam_id:
            raise SteamError(f"SetLobbyData failed for {key!r}")
        lobby.data[key] = value

    def set_lobby_owner(self, lobby_id, new_owner):
        self.calls.append(("set_lobby_owner", lobby_id, new_owner))
        lobby = self._lobby(lobby_id)
        if lobby.owner != self.steam_id or new_owner not in lobby.members:
            raise SteamError("SetLobbyOwner failed")
        lobby.owner = new_owner

    def friend_persona_name(self, steam_id):
        client = self.world.clients.get(steam_id)
        return client.persona_name if client is not None else ""

    def open_invite_dialog(self, connect):
        self.calls.append(("open_invite_dialog", connect))

    def overlay_needs_present(self):
        return False

    # Connections

    def create_listen_socket(self, virtual_port=0):
        handle = self.world.handle()
        self.listen_sockets.add(handle)
        return handle

    def close_listen_socket(self, listen_socket):
        self.listen_sockets.discard(listen_socket)
        return True

    def connect_p2p(self, remote_steam_id, virtual_port=0):
        handle = self.world.handle()
        connection = Connection(self.steam_id, remote_steam_id, handle)
        self.connections[handle] = connection
        remote = self.world.clients.get(remote_steam_id)
        if remote is None or not remote.running or not remote.listen_sockets:
            connection.open = False
            self.queue.append(
                status(handle, ConnectionState.PROBLEM_DETECTED_LOCALLY, remote_steam_id)
            )
            return handle
        remote_handle = self.world.handle()
        connection.remote_handle = remote_handle
        remote.connections[remote_handle] = Connection(
            remote_steam_id, self.steam_id, remote_handle, handle
        )
        listen = next(iter(remote.listen_sockets))
        remote.queue.append(
            status(remote_handle, ConnectionState.CONNECTING, self.steam_id, listen)
        )
        return handle

    def accept_connection(self, handle):
        connection = self.connections[handle]
        other = self.world.clients[connection.remote]
        remote = other.connections.get(connection.remote_handle)
        if remote is None or not remote.open:
            raise SteamError("AcceptConnection failed: INVALID_STATE")
        connection.connected = remote.connected = True
        self.queue.append(status(handle, ConnectionState.CONNECTED, connection.remote))
        other.queue.append(status(remote.handle, ConnectionState.CONNECTED, self.steam_id))

    def close_connection(self, handle, debug="", linger=False):
        connection = self.connections.pop(handle, None)
        if connection is None:
            return False
        self.inbox.pop(handle, None)
        other = self.world.clients.get(connection.remote)
        remote = other.connections.get(connection.remote_handle) if other else None
        if connection.open and remote is not None and remote.open:
            remote.open = remote.connected = False
            other.queue.append(status(remote.handle, ConnectionState.CLOSED_BY_PEER, self.steam_id))
        return True

    def send_message(self, handle, data, reliable=True):
        connection = self.connections.get(handle)
        if connection is None or not connection.connected:
            raise SteamError("SendMessageToConnection failed: NO_CONNECTION")
        other = self.world.clients[connection.remote]
        other.inbox.setdefault(connection.remote_handle, []).append(bytes(data))

    def receive_messages(self, handle):
        return self.inbox.pop(handle, [])

    # Going away

    def crash(self):
        """The process dies: Steam notices the member is gone, and its
        connections drop."""
        self.running = False
        for handle in list(self.connections):
            connection = self.connections.pop(handle)
            other = self.world.clients.get(connection.remote)
            remote = other.connections.get(connection.remote_handle) if other else None
            if remote is not None and remote.open:
                remote.open = remote.connected = False
                other.queue.append(
                    status(remote.handle, ConnectionState.PROBLEM_DETECTED_LOCALLY, self.steam_id)
                )
        for lobby_id in list(self.lobbies):
            self.world.remove_member(lobby_id, self.steam_id, ChatMemberStateChange.DISCONNECTED)
        self.lobbies.clear()

    def close(self):
        self.calls.append(("close",))
        if self.running:
            for lobby_id in list(self.lobbies):
                self.world.remove_member(lobby_id, self.steam_id, ChatMemberStateChange.LEFT)
            self.lobbies.clear()
        self.running = False
