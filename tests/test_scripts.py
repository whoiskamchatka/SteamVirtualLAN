import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from steamlan.steam import ConnectionState, SteamCallback, SteamError
from steamlan.steam.native import (
    CONNECTION_STATUS_CHANGED,
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
    )
    monkeypatch.setattr(modules.host, "POLL_INTERVAL", 0)
    monkeypatch.setattr(modules.guest, "POLL_INTERVAL", 0)
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

    def send_message(self, connection, data):
        self.sent.append((connection, data))

    def receive_messages(self, connection):
        return [self.inbox.pop(0)] if self.inbox else []

    def close_connection(self, connection, debug=""):
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
