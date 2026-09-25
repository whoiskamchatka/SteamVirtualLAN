import pytest

from steamlan.steam import (
    ChatMemberStateChange,
    ConnectionState,
    LobbySession,
    SteamCallback,
    SteamError,
    initiates,
)
from steamlan.steam.native import (
    CONNECTION_STATUS_CHANGED,
    LOBBY_CHAT_UPDATE,
    LobbyChatUpdate,
    SteamNetConnectionStatusChangedCallback,
)

LOBBY_ID = 109775240917097000
OTHER_LOBBY_ID = 109775240917097999
LISTEN_SOCKET = 0x55
LOW = 76561197960265729
ME = 76561197960265730
HIGH = 76561197960265731
HIGHER = 76561197960265732


def member_update(user_id, state=ChatMemberStateChange.ENTERED, lobby_id=LOBBY_ID):
    return SteamCallback(
        LOBBY_CHAT_UPDATE, bytes(LobbyChatUpdate(lobby_id, user_id, user_id, state))
    )


def status(connection, state, remote, listen_socket=0, end_reason=0, end_debug=b""):
    changed = SteamNetConnectionStatusChangedCallback()
    changed.m_hConn = connection
    info = changed.m_info
    if remote is not None:
        info.m_identityRemote.m_eType = 16
        info.m_identityRemote.m_cbSize = 8
        info.m_identityRemote.m_data[:8] = remote.to_bytes(8, "little")
    info.m_hListenSocket = listen_socket
    info.m_eState = state
    info.m_eEndReason = end_reason
    info.m_szEndDebug = end_debug
    return SteamCallback(CONNECTION_STATUS_CHANGED, bytes(changed))


def incoming(connection, remote):
    return status(connection, ConnectionState.CONNECTING, remote, LISTEN_SOCKET)


class FakeSteam:
    def __init__(self, members=(ME,), steam_id=ME):
        self.steam_id = steam_id
        self.members = list(members)
        self.frames = []
        self.inbox = {}
        self.next_connection = 100
        self.connects = []
        self.accepted = []
        self.closed = []
        self.sent = []

    def lobby_members(self, lobby_id):
        assert lobby_id == LOBBY_ID
        return list(self.members)

    def run_callbacks(self):
        return self.frames.pop(0) if self.frames else []

    def connect_p2p(self, remote_steam_id, virtual_port=0):
        assert virtual_port == 0
        self.next_connection += 1
        self.connects.append((remote_steam_id, self.next_connection))
        return self.next_connection

    def accept_connection(self, connection):
        self.accepted.append(connection)

    def close_connection(self, connection, debug=""):
        self.closed.append(connection)
        return True

    def send_message(self, connection, data):
        self.sent.append((connection, data))

    def receive_messages(self, connection):
        return self.inbox.pop(connection, [])


def session(steam, log=None):
    return LobbySession(steam, LOBBY_ID, LISTEN_SOCKET, log=log or (lambda message: None))


def test_lower_steam_id_initiates():
    assert initiates(LOW, HIGH) is True
    assert initiates(HIGH, LOW) is False


@pytest.mark.parametrize(("a", "b"), [(LOW, HIGH), (HIGH, LOW), (ME, HIGHER), (1, 2**64 - 1)])
def test_exactly_one_side_initiates(a, b):
    assert initiates(a, b) != initiates(b, a)


def test_no_connection_to_self():
    with pytest.raises(ValueError):
        initiates(ME, ME)


def test_local_member_is_not_a_peer():
    assert session(FakeSteam(members=[ME])).peers == {}


def test_remote_members_become_peers():
    s = session(FakeSteam(members=[LOW, ME, HIGH]))

    assert sorted(s.peers) == [LOW, HIGH]
    assert s.peers[LOW].initiator is False
    assert s.peers[HIGH].initiator is True


def test_member_update_adds_peer_once():
    steam = FakeSteam()
    s = session(steam)
    steam.members.append(HIGH)
    steam.frames = [[member_update(HIGH)], [member_update(HIGH)]]

    s.poll()
    s.poll()

    assert list(s.peers) == [HIGH]
    assert [remote for remote, _ in steam.connects] == [HIGH]


def test_member_update_for_other_lobby_is_ignored():
    steam = FakeSteam()
    s = session(steam)
    steam.members.append(HIGH)
    steam.frames = [[member_update(HIGH, lobby_id=OTHER_LOBBY_ID)]]

    s.poll()

    assert s.peers == {}


def test_initiator_connects_once():
    steam = FakeSteam(members=[ME, HIGH])
    s = session(steam)

    s.poll()
    s.poll()

    assert steam.connects == [(HIGH, 101)]
    assert s.peers[HIGH].connection == 101
    assert not s.peers[HIGH].connected


def test_non_initiator_does_not_connect():
    steam = FakeSteam(members=[LOW, ME])
    s = session(steam)

    s.poll()

    assert steam.connects == []
    assert s.peers[LOW].connection == 0


def test_outgoing_connection_becomes_connected():
    steam = FakeSteam(members=[ME, HIGH])
    s = session(steam)
    s.poll()
    steam.frames = [
        [status(101, ConnectionState.CONNECTING, HIGH)],
        [status(101, ConnectionState.FINDING_ROUTE, HIGH)],
        [status(101, ConnectionState.CONNECTED, HIGH)],
    ]

    s.poll()
    s.poll()
    assert s.connected_peers() == []
    s.poll()

    assert s.connected_peers() == [HIGH]
    assert steam.connects == [(HIGH, 101)]


def test_incoming_connection_from_member_is_accepted():
    steam = FakeSteam(members=[LOW, ME])
    s = session(steam)
    steam.frames = [[incoming(7, LOW)], [status(7, ConnectionState.CONNECTED, LOW, LISTEN_SOCKET)]]

    s.poll()
    s.poll()

    assert steam.accepted == [7]
    assert s.peers[LOW].connection == 7
    assert s.connected_peers() == [LOW]


def test_incoming_connection_from_new_member_before_its_lobby_update():
    steam = FakeSteam()
    s = session(steam)
    steam.members.append(LOW)
    steam.frames = [[incoming(7, LOW)]]

    s.poll()

    assert steam.accepted == [7]
    assert s.peers[LOW].connection == 7


@pytest.mark.parametrize("remote", [HIGHER + 100, None])
def test_incoming_connection_from_non_member_is_rejected(remote):
    steam = FakeSteam(members=[LOW, ME])
    s = session(steam)
    steam.frames = [[incoming(7, remote)]]

    s.poll()

    assert steam.accepted == []
    assert steam.closed == [7]
    assert s.peers[LOW].connection == 0


def test_incoming_connection_from_peer_we_initiate_to_is_rejected():
    steam = FakeSteam(members=[ME, HIGH])
    s = session(steam)
    s.poll()
    steam.frames = [[incoming(7, HIGH)]]

    s.poll()

    assert steam.accepted == []
    assert steam.closed == [7]
    assert s.peers[HIGH].connection == 101


def test_second_incoming_connection_is_rejected():
    steam = FakeSteam(members=[LOW, ME])
    s = session(steam)
    steam.frames = [[incoming(7, LOW)], [incoming(8, LOW)]]

    s.poll()
    s.poll()

    assert steam.accepted == [7]
    assert steam.closed == [8]
    assert s.peers[LOW].connection == 7


def test_incoming_on_other_listen_socket_is_not_accepted():
    steam = FakeSteam(members=[LOW, ME])
    s = session(steam)
    steam.frames = [[status(7, ConnectionState.CONNECTING, LOW, listen_socket=0x99)]]

    s.poll()

    assert steam.accepted == []


def test_failed_accept_closes_connection():
    steam = FakeSteam(members=[LOW, ME])

    def accept_connection(connection):
        raise SteamError("AcceptConnection failed: INVALID_STATE")

    steam.accept_connection = accept_connection
    s = session(steam)
    steam.frames = [[incoming(7, LOW)]]

    s.poll()

    assert steam.closed == [7]
    assert s.peers[LOW].connection == 0


def test_connection_with_wrong_identity_is_closed():
    steam = FakeSteam(members=[ME, HIGH])
    s = session(steam)
    s.poll()
    steam.frames = [[status(101, ConnectionState.CONNECTED, HIGHER)]]

    s.poll()

    assert steam.closed == [101]
    assert s.connected_peers() == []
    assert str(HIGHER) in s.peers[HIGH].ended


@pytest.mark.parametrize(
    "state", [ConnectionState.CLOSED_BY_PEER, ConnectionState.PROBLEM_DETECTED_LOCALLY]
)
def test_ended_connection_is_cleared_and_not_retried(state):
    log = []
    steam = FakeSteam(members=[ME, HIGH])
    s = session(steam, log=log.append)
    s.poll()
    steam.frames = [
        [status(101, ConnectionState.CONNECTED, HIGH)],
        [status(101, state, HIGH, end_reason=5008, end_debug=b"timed out")],
        [],
    ]

    s.poll()
    s.poll()
    s.poll()

    peer = s.peers[HIGH]
    assert (peer.connection, peer.connected) == (0, False)
    assert "5008" in peer.ended and "timed out" in peer.ended
    assert steam.closed == [101]
    assert steam.connects == [(HIGH, 101)]
    with pytest.raises(SteamError, match="not connected"):
        s.send(HIGH, b"x")
    assert any("timed out" in line for line in log)


def test_leaving_member_closes_its_connection():
    steam = FakeSteam(members=[ME, HIGH])
    s = session(steam)
    s.poll()
    steam.members.remove(HIGH)
    steam.frames = [[member_update(HIGH, ChatMemberStateChange.LEFT)]]

    s.poll()

    assert s.peers == {}
    assert steam.closed == [101]


def test_multiple_peers_are_independent():
    steam = FakeSteam(members=[LOW, ME, HIGH, HIGHER])
    s = session(steam)
    steam.frames = [
        [incoming(7, LOW)],
        [
            status(101, ConnectionState.CONNECTED, HIGH),
            status(7, ConnectionState.CONNECTED, LOW, LISTEN_SOCKET),
        ],
    ]

    s.poll()
    s.poll()

    assert sorted(remote for remote, _ in steam.connects) == [HIGH, HIGHER]
    assert steam.accepted == [7]
    assert sorted(s.connected_peers()) == [LOW, HIGH]


def test_messages_are_received_only_from_connected_peers():
    steam = FakeSteam(members=[ME, HIGH])
    s = session(steam)
    s.poll()
    steam.inbox[101] = [b"early"]

    assert s.poll() == []

    steam.frames = [[status(101, ConnectionState.CONNECTED, HIGH)]]
    steam.inbox[101] = [b"one", b"\x00two"]
    assert s.poll() == [(HIGH, b"one"), (HIGH, b"\x00two")]


def test_send_to_connected_peer():
    steam = FakeSteam(members=[ME, HIGH])
    s = session(steam)
    s.poll()
    steam.frames = [[status(101, ConnectionState.CONNECTED, HIGH)]]
    s.poll()

    s.send(HIGH, b"payload")

    assert steam.sent == [(101, b"payload")]


def test_send_to_unknown_peer():
    with pytest.raises(SteamError, match="not connected"):
        session(FakeSteam()).send(HIGH, b"x")


def test_close_closes_every_connection():
    steam = FakeSteam(members=[LOW, ME, HIGH])
    s = session(steam)
    steam.frames = [[incoming(7, LOW)]]
    s.poll()

    s.close()

    assert sorted(steam.closed) == [7, 101]
    assert all(peer.connection == 0 for peer in s.peers.values())

    s.close()
    assert sorted(steam.closed) == [7, 101]


def test_unknown_ended_connection_is_freed():
    steam = FakeSteam()
    s = session(steam)
    steam.frames = [[status(55, ConnectionState.CLOSED_BY_PEER, HIGH)]]

    s.poll()

    assert steam.closed == [55]
