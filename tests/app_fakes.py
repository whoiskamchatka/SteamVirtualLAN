"""A fake SteamClient for the app tests, with helpers for the callbacks it returns."""

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
        self.closed = []
        self.fail = set()
        self.running = True
        self.overlay_enabled = True
        self.needs_present = False

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
        self.lobby_metadata[key] = value

    def lobby_data(self, lobby_id, key):
        return self.lobby_metadata.get(key, "")

    def lobby_owner(self, lobby_id):
        return self.owner

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
        self.closed.append((connection, linger))
        return True

    def send_message(self, connection, data):
        self.sent.append((connection, data))

    def receive_messages(self, connection):
        return self.inbox.pop(connection, [])

    def close(self):
        self._record("close")
        self.running = False

    def called(self, name):
        return [call[1:] for call in self.calls if call[0] == name]
