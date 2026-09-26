import importlib
import ipaddress
from pathlib import Path
from types import SimpleNamespace

import pytest

from steamlan.steam import ConnectionState, LobbySession, Peer, SteamCallback, SteamError
from steamlan.steam.native import (
    CONNECTION_STATUS_CHANGED,
    GAME_LOBBY_JOIN_REQUESTED,
    GameLobbyJoinRequested,
    SteamNetConnectionStatusChangedCallback,
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
HOST_ID = 76561197960265729
GUEST_ID = 76561197960265730
LISTEN_SOCKET = 0x55


@pytest.fixture
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    modules = SimpleNamespace(
        local=importlib.import_module("local_steam"),
        host=importlib.import_module("p2p_host"),
        guest=importlib.import_module("p2p_guest"),
        lobby=importlib.import_module("lobby_p2p"),
        adapter=importlib.import_module("check_adapter"),
    )
    monkeypatch.setattr(modules.host, "POLL_INTERVAL", 0)
    monkeypatch.setattr(modules.guest, "POLL_INTERVAL", 0)
    monkeypatch.setattr(modules.lobby, "POLL_INTERVAL", 0)
    return modules


def status(connection, state, listen_socket=0, remote=GUEST_ID):
    changed = SteamNetConnectionStatusChangedCallback()
    changed.m_hConn = connection
    info = changed.m_info
    info.m_identityRemote.m_eType = 16
    info.m_identityRemote.m_cbSize = 8
    info.m_identityRemote.m_data[:8] = remote.to_bytes(8, "little")
    info.m_hListenSocket = listen_socket
    info.m_eState = state
    return SteamCallback(CONNECTION_STATUS_CHANGED, bytes(changed))


class FakeSteam:
    def __init__(self, frames, inbox=()):
        self.frames = list(frames)
        self.inbox = list(inbox)
        self.accepted = []
        self.sent = []
        self.closed = []

    def run_callbacks(self):
        return self.frames.pop(0) if self.frames else []

    def accept_connection(self, connection):
        self.accepted.append(connection)

    def send_message(self, connection, data, reliable=True):
        self.sent.append((connection, data))

    def receive_messages(self, connection):
        return [self.inbox.pop(0)] if self.inbox else []

    def close_connection(self, connection, debug="", linger=False):
        self.closed.append(connection)
        return True


@pytest.mark.parametrize("text", [str(HOST_ID), "1", str(2**64 - 1)])
def test_parse_steam_id(scripts, text):
    assert scripts.local.parse_steam_id(text) == int(text)


@pytest.mark.parametrize("text", ["", "0", "-5", "abc", "1.5", "²", str(2**64)])
def test_parse_invalid_steam_id(scripts, text):
    with pytest.raises(ValueError, match="not a SteamID"):
        scripts.local.parse_steam_id(text)


def test_host_accepts_incoming_and_exchanges(scripts):
    steam = FakeSteam(
        [
            [status(9, ConnectionState.CONNECTING)],  # someone else's outgoing connection
            [status(1, ConnectionState.CONNECTING, LISTEN_SOCKET)],
            [status(1, ConnectionState.CONNECTED, LISTEN_SOCKET)],
        ],
        inbox=[b"hello from guest"],
    )
    connections = []

    reply = scripts.host.exchange(steam, connections)

    assert reply == b"hello from guest"
    assert steam.accepted == [1]
    assert steam.sent == [(1, b"hello from host")]
    assert connections == [1]


def test_host_rejects_second_peer(scripts):
    steam = FakeSteam(
        [
            [status(1, ConnectionState.CONNECTING, LISTEN_SOCKET)],
            [status(2, ConnectionState.CONNECTING, LISTEN_SOCKET, remote=HOST_ID + 5)],
            [status(1, ConnectionState.CONNECTED, LISTEN_SOCKET)],
        ],
        inbox=[b"hello from guest"],
    )
    connections = []

    scripts.host.exchange(steam, connections)

    assert steam.accepted == [1]
    assert steam.closed == [2]
    assert connections == [1, 2]


@pytest.mark.parametrize(
    "state", [ConnectionState.CLOSED_BY_PEER, ConnectionState.PROBLEM_DETECTED_LOCALLY]
)
def test_host_connection_ends_early(scripts, state):
    steam = FakeSteam(
        [
            [status(1, ConnectionState.CONNECTING, LISTEN_SOCKET)],
            [status(1, state, LISTEN_SOCKET)],
        ]
    )

    with pytest.raises(SteamError, match=f"connection ended \\({state.name}"):
        scripts.host.exchange(steam, [])


def test_host_timeout(scripts, monkeypatch):
    monkeypatch.setattr(scripts.host, "TIMEOUT", 0)

    with pytest.raises(SteamError, match="no reply from a guest"):
        scripts.host.exchange(FakeSteam([]), [])


def test_guest_receives_hello(scripts):
    steam = FakeSteam(
        [
            [status(7, ConnectionState.CONNECTING, remote=HOST_ID)],
            [status(7, ConnectionState.FINDING_ROUTE, remote=HOST_ID)],
            [status(7, ConnectionState.CONNECTED, remote=HOST_ID)],
        ],
        inbox=[b"hello from host"],
    )

    assert scripts.guest.receive_hello(steam, 7) == b"hello from host"


def test_guest_receives_only_after_connected(scripts):
    steam = FakeSteam([[], [], [status(7, ConnectionState.CONNECTED)]], inbox=[b"hello from host"])
    frames_left_at_receive = []
    receive = steam.receive_messages

    def track_receive(connection):
        frames_left_at_receive.append(len(steam.frames))
        return receive(connection)

    steam.receive_messages = track_receive

    assert scripts.guest.receive_hello(steam, 7) == b"hello from host"
    assert frames_left_at_receive == [0]


def test_guest_connection_fails(scripts):
    steam = FakeSteam([[status(7, ConnectionState.PROBLEM_DETECTED_LOCALLY)]])

    with pytest.raises(SteamError, match="PROBLEM_DETECTED_LOCALLY"):
        scripts.guest.receive_hello(steam, 7)


def test_guest_timeout_before_connecting(scripts, monkeypatch):
    monkeypatch.setattr(scripts.guest, "TIMEOUT", 0)

    with pytest.raises(SteamError, match="could not connect"):
        scripts.guest.receive_hello(FakeSteam([]), 7)


def test_guest_waits_for_host_close(scripts):
    steam = FakeSteam(
        [
            [],
            [status(8, ConnectionState.CLOSED_BY_PEER)],
            [status(7, ConnectionState.CLOSED_BY_PEER)],
        ]
    )

    scripts.guest.wait_for_host_close(steam, 7)

    assert steam.frames == []


def test_guest_host_close_problem(scripts):
    steam = FakeSteam([[status(7, ConnectionState.PROBLEM_DETECTED_LOCALLY)]])

    with pytest.raises(SteamError, match="PROBLEM_DETECTED_LOCALLY"):
        scripts.guest.wait_for_host_close(steam, 7)


def test_guest_host_close_timeout(scripts, monkeypatch):
    monkeypatch.setattr(scripts.guest, "CLOSE_TIMEOUT", 0)

    with pytest.raises(SteamError, match="did not confirm"):
        scripts.guest.wait_for_host_close(FakeSteam([]), 7)


LOBBY_ID = 109775240917097000


class FakeSession:
    def __init__(self, peers, polls=()):
        self.peers = {steam_id: Peer(steam_id, initiator) for steam_id, initiator in peers}
        self.polls = list(polls)
        self.connected = set()
        self.sent = []

    def poll(self):
        return self.polls.pop(0) if self.polls else []

    def connected_peers(self):
        return sorted(self.connected)

    def send(self, steam_id, data):
        self.sent.append((steam_id, data))


def test_initiator_sends_hello_once_after_connected(scripts):
    session = FakeSession([(GUEST_ID, True)])
    greeted = set()
    log = []

    scripts.lobby.hello_step(session, greeted, log.append)
    assert session.sent == []

    session.connected.add(GUEST_ID)
    scripts.lobby.hello_step(session, greeted, log.append)
    scripts.lobby.hello_step(session, greeted, log.append)

    assert session.sent == [(GUEST_ID, scripts.lobby.HELLO)]
    assert log == [f"Sent hello to {GUEST_ID}"]


def test_accepting_side_answers_hello(scripts):
    session = FakeSession([(HOST_ID, False)], polls=[[(HOST_ID, scripts.lobby.HELLO)]])
    session.connected.add(HOST_ID)
    log = []

    scripts.lobby.hello_step(session, set(), log.append)

    assert session.sent == [(HOST_ID, scripts.lobby.HELLO_ACK)]
    assert f"P2P test succeeded with {HOST_ID}" in log


def test_initiator_receives_ack(scripts):
    session = FakeSession([(GUEST_ID, True)], polls=[[(GUEST_ID, scripts.lobby.HELLO_ACK)]])
    session.connected.add(GUEST_ID)
    log = []

    scripts.lobby.hello_step(session, {GUEST_ID}, log.append)

    assert session.sent == []
    assert log == [f"Received ack from {GUEST_ID}", f"P2P test succeeded with {GUEST_ID}"]


def test_other_data_is_only_reported(scripts):
    session = FakeSession([(HOST_ID, False)], polls=[[(HOST_ID, b"\x00\x01")]])
    log = []

    scripts.lobby.hello_step(session, set(), log.append)

    assert session.sent == []
    assert log == [f"Received 2 bytes from {HOST_ID}"]


class FakeLobbySteam:
    def __init__(self, frames=()):
        self.frames = list(frames)
        self.calls = []

    def run_callbacks(self):
        return self.frames.pop(0) if self.frames else []

    def create_lobby(self, max_members):
        self.calls.append(("create_lobby", max_members))
        return LOBBY_ID

    def invite_to_lobby(self, lobby_id, friend_id):
        self.calls.append(("invite_to_lobby", lobby_id, friend_id))

    def join_lobby(self, lobby_id):
        self.calls.append(("join_lobby", lobby_id))
        return lobby_id


def join_request(lobby_id=LOBBY_ID, friend_id=HOST_ID):
    return SteamCallback(
        GAME_LOBBY_JOIN_REQUESTED, bytes(GameLobbyJoinRequested(lobby_id, friend_id))
    )


@pytest.mark.parametrize(
    ("target", "calls"),
    [
        (None, [("create_lobby", 8)]),
        (GUEST_ID, [("create_lobby", 8), ("invite_to_lobby", LOBBY_ID, GUEST_ID)]),
    ],
)
def test_host_enters_new_lobby(scripts, target, calls):
    steam = FakeLobbySteam()

    assert scripts.lobby.enter_lobby(steam, "host", target) == LOBBY_ID
    assert steam.calls == calls


def test_guest_joins_given_lobby(scripts):
    steam = FakeLobbySteam(frames=[[join_request(lobby_id=LOBBY_ID + 1)]])

    assert scripts.lobby.enter_lobby(steam, "guest", LOBBY_ID) == LOBBY_ID
    assert steam.calls == [("join_lobby", LOBBY_ID)]


def test_guest_joins_lobby_from_accepted_invite(scripts):
    steam = FakeLobbySteam(frames=[[], [SteamCallback(304, b"")], [join_request()]])

    assert scripts.lobby.enter_lobby(steam, "guest", None) == LOBBY_ID
    assert steam.calls == [("join_lobby", LOBBY_ID)]


class Network:
    """Two or more fake Steam clients whose P2P calls reach each other."""

    def __init__(self, *steam_ids):
        self.members = list(steam_ids)
        self.clients = {steam_id: LinkedSteam(steam_id, self) for steam_id in steam_ids}
        self.links = {}
        self.connects = []
        self.last_handle = 0

    def new_handle(self):
        self.last_handle += 1
        return self.last_handle


class LinkedSteam:
    def __init__(self, steam_id, network):
        self.steam_id = steam_id
        self.network = network
        self.pending = []
        self.inbox = {}

    def lobby_members(self, lobby_id):
        return list(self.network.members)

    def run_callbacks(self):
        callbacks, self.pending = self.pending, []
        return callbacks

    def connect_p2p(self, remote_steam_id, virtual_port=0):
        network = self.network
        mine, theirs = network.new_handle(), network.new_handle()
        network.links[(self.steam_id, mine)] = (remote_steam_id, theirs)
        network.links[(remote_steam_id, theirs)] = (self.steam_id, mine)
        network.connects.append(self.steam_id)
        self.pending.append(status(mine, ConnectionState.CONNECTING, 0, remote_steam_id))
        network.clients[remote_steam_id].pending.append(
            status(theirs, ConnectionState.CONNECTING, LISTEN_SOCKET, self.steam_id)
        )
        return mine

    def accept_connection(self, connection):
        remote, theirs = self.network.links[(self.steam_id, connection)]
        self.pending.append(status(connection, ConnectionState.CONNECTED, LISTEN_SOCKET, remote))
        self.network.clients[remote].pending.append(
            status(theirs, ConnectionState.CONNECTED, 0, self.steam_id)
        )

    def send_message(self, connection, data, reliable=True):
        remote, theirs = self.network.links[(self.steam_id, connection)]
        self.network.clients[remote].inbox.setdefault(theirs, []).append(data)

    def receive_messages(self, connection):
        return self.inbox.pop(connection, [])

    def close_connection(self, connection, debug="", linger=False):
        return True


@pytest.mark.parametrize(("first", "second"), [(HOST_ID, GUEST_ID), (GUEST_ID, HOST_ID)])
def test_two_members_connect_and_exchange_hello(scripts, first, second):
    network = Network(first, second)
    logs = {steam_id: [] for steam_id in (first, second)}
    sessions = {
        steam_id: LobbySession(client, LOBBY_ID, LISTEN_SOCKET, log=logs[steam_id].append)
        for steam_id, client in network.clients.items()
    }
    greeted = {steam_id: set() for steam_id in sessions}

    for _ in range(6):
        for steam_id, session in sessions.items():
            scripts.lobby.hello_step(session, greeted[steam_id], logs[steam_id].append)

    assert network.connects == [min(first, second)]
    assert f"P2P test succeeded with {second}" in logs[first]
    assert f"P2P test succeeded with {first}" in logs[second]


def ping(source="10.77.0.1", destination="10.77.0.2", icmp_type=8, protocol=1):
    icmp = bytes([icmp_type, 0, 0, 0]) + (1).to_bytes(2, "big") + (7).to_bytes(2, "big") + b"abcd"
    header = bytearray(20)
    header[0] = 0x45
    header[2:4] = (20 + len(icmp)).to_bytes(2, "big")
    header[4:6] = (0x1234).to_bytes(2, "big")
    header[8] = 128
    header[9] = protocol
    header[12:16] = ipaddress.IPv4Address(source).packed
    header[16:20] = ipaddress.IPv4Address(destination).packed
    return bytes(header) + icmp


def test_internet_checksum(scripts):
    # RFC 1071 example words.
    data = bytes.fromhex("0001f203f4f5f6f7")
    total = scripts.adapter.checksum(data)

    assert total == 0x220D
    assert scripts.adapter.checksum(data + total.to_bytes(2, "big")) == 0


def test_echo_reply(scripts):
    request = ping()

    reply = scripts.adapter.echo_reply(request)

    assert reply[12:16] == request[16:20]
    assert reply[16:20] == request[12:16]
    assert reply[20] == 0
    assert reply[24:] == request[24:]
    assert reply[4:6] == request[4:6]
    assert scripts.adapter.checksum(reply[:20]) == 0
    assert scripts.adapter.checksum(reply[20:]) == 0
    assert len(reply) == int.from_bytes(reply[2:4], "big")


@pytest.mark.parametrize(
    "packet",
    [
        ping(destination="10.77.1.2"),
        ping(destination="10.77.0.1"),
        ping(destination="10.77.0.255"),
        ping(source="10.77.0.9"),
        ping(icmp_type=0),
        ping(protocol=17),
        ping()[:24],
        b"\x60" + b"\0" * 39,
        b"",
    ],
    ids=[
        "other-subnet",
        "own-address",
        "broadcast",
        "not-from-this-machine",
        "already-a-reply",
        "udp",
        "truncated",
        "ipv6",
        "empty",
    ],
)
def test_no_echo_reply(scripts, packet):
    assert scripts.adapter.echo_reply(packet) is None


def test_elevation_relaunches_with_the_same_options(scripts, monkeypatch):
    launched = []
    monkeypatch.setattr(
        scripts.adapter, "relaunch_as_admin", lambda args: launched.append(args) or True
    )

    assert scripts.adapter.elevate(["--reply"]) == 0
    assert launched[0][1:] == ["--reply", "--pause"]


def test_elevation_refused(scripts, monkeypatch):
    monkeypatch.setattr(scripts.adapter, "relaunch_as_admin", lambda args: False)

    assert scripts.adapter.elevate([]) == 1


def test_diagnostic_stops_before_elevating_when_wintun_is_unavailable(scripts, monkeypatch):
    def unavailable():
        raise scripts.adapter.WintunLoadError("could not download")

    monkeypatch.setattr(scripts.adapter, "ensure_wintun", unavailable)
    monkeypatch.setattr(scripts.adapter, "relaunch_as_admin", lambda args: pytest.fail("elevated"))
    monkeypatch.setattr(scripts.adapter.sys, "argv", ["check_adapter.py", "--reply"])

    assert scripts.adapter.main() == 1


def test_diagnostic_elevates_after_preparing_wintun(scripts, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        scripts.adapter, "ensure_wintun", lambda: calls.append("wintun") or tmp_path
    )
    monkeypatch.setattr(scripts.adapter, "is_admin", lambda: False)
    monkeypatch.setattr(
        scripts.adapter,
        "relaunch_as_admin",
        lambda args: calls.append(("elevate", args[1:])) or True,
    )
    monkeypatch.setattr(scripts.adapter.sys, "argv", ["check_adapter.py", "--reply"])

    assert scripts.adapter.main() == 0
    assert calls == ["wintun", ("elevate", ["--reply", "--pause"])]


def test_scripts_load_steam_api_from_the_repository_root(scripts, monkeypatch):
    monkeypatch.delenv("STEAMLAN_STEAM_API_DIR", raising=False)
    monkeypatch.setattr(scripts.local, "prepare_app_id", lambda: None)

    steam = scripts.local.local_client()

    assert Path(steam.dll_path) == SCRIPTS.parent / "steam_api64.dll"


class FakeSocket:
    def __init__(self, family, kind, port=40000, fail_bind=False):
        self.options = {}
        self.bound = None
        self.sent = []
        self.port = port
        self.fail_bind = fail_bind
        self.closed = False

    def setsockopt(self, level, option, value):
        self.options[(level, option)] = value

    def bind(self, address):
        if self.fail_bind and address[0] != "0.0.0.0":
            raise OSError("The requested address is not valid in its context")
        self.bound = address

    def getsockname(self):
        return (self.bound[0], self.port)

    def sendto(self, data, address):
        self.sent.append((data, address))

    def close(self):
        self.closed = True


def fake_sockets(**options):
    made = []

    def factory(family, kind):
        made.append(FakeSocket(family, kind, port=40000 + len(made), **options))
        return made[-1]

    return made, factory


def test_discovery_probe_sockets(scripts):
    import socket

    made, factory = fake_sockets()
    scripts.adapter.DiscoveryCheck(factory=factory)

    bound_multicast = made[2]
    assert bound_multicast.bound == ("10.77.0.1", 0)
    assert bound_multicast.options[(socket.IPPROTO_IP, socket.IP_MULTICAST_IF)] == (
        socket.inet_aton("10.77.0.1")
    )
    unbound_multicast = made[4]
    assert unbound_multicast.bound == ("0.0.0.0", 0)
    assert (socket.IPPROTO_IP, socket.IP_MULTICAST_IF) not in unbound_multicast.options
    assert all(s.options[(socket.SOL_SOCKET, socket.SO_BROADCAST)] == 1 for s in made)


def test_discovery_probes_are_sent_every_interval(scripts):
    made, factory = fake_sockets()
    now = [0.0]
    check = scripts.adapter.DiscoveryCheck(factory=factory, clock=lambda: now[0])

    check.send_due()
    check.send_due()
    now[0] = scripts.adapter.DISCOVERY_INTERVAL
    check.send_due()

    probes = scripts.adapter.PROBES
    for probe, sock in zip(probes, made, strict=True):
        assert (
            sock.sent == [(probe.payload, (probe.destination, scripts.adapter.DISCOVERY_PORT))] * 2
        )


def test_discovery_recognizes_probes_that_reach_the_adapter(scripts):
    from app_fakes import udp_packet

    made, factory = fake_sockets()
    check = scripts.adapter.DiscoveryCheck(factory=factory)
    broadcast = scripts.adapter.PROBES[0]
    port = made[0].port

    arrived = udp_packet("10.77.0.1", "10.77.0.255", port, 47999, broadcast.payload)
    note = check.observe(arrived)

    assert note == (
        f"  probe 'bound, network broadcast': 10.77.0.1:{port} -> 10.77.0.255:47999, "
        "broadcast, ports and data unchanged"
    )
    changed = udp_packet(
        "10.77.0.1", "239.255.255.250", 1, 47999, scripts.adapter.PROBES[2].payload
    )
    assert "CHANGED" in check.observe(changed)
    assert check.observe(udp_packet("10.77.0.1", "10.77.0.255", 5, 6, b"a game")) is None
    assert check.observe(b"\x60" + bytes(39)) is None

    report = "\n".join(check.report())
    assert "network broadcast to 10.77.0.255: arrived in the adapter" in report
    assert "unbound, limited broadcast to 255.255.255.255: never arrived" in report


def test_discovery_reports_probes_that_could_not_be_sent(scripts):
    made, factory = fake_sockets(fail_bind=True)
    check = scripts.adapter.DiscoveryCheck(factory=factory)
    check.send_due()
    check.close()

    report = "\n".join(check.report())
    assert "bound, multicast to 239.255.255.250: could not be sent" in report
    assert "unbound, multicast to 239.255.255.250: never arrived" in report
    assert all(sock.closed for sock in made if sock.bound)
