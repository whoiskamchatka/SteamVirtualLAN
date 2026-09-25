import pytest
from app_fakes import (
    GUEST,
    HOST,
    LISTEN_SOCKET,
    LOBBY,
    OTHER,
    STRANGER,
    FakeSteam,
    incoming,
    member_update,
    status,
)

from steamlan.app import access
from steamlan.app.network import AUTH_TIMEOUT, MAX_FAILURES, NetworkSession
from steamlan.steam import ChatMemberStateChange, ConnectionState

CODE = "7K2QDM9XTE"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def host_network(members=(HOST, GUEST), clock=None):
    steam = FakeSteam(HOST, members, names={GUEST: "Guest", OTHER: "Other"})
    network = NetworkSession(steam, LOBBY, LISTEN_SOCKET, HOST, CODE, clock or Clock())
    return steam, network


def connect_guest(steam, network):
    """The host has the lower SteamID, so it connects to the guest."""
    network.process([])
    (connection,) = [call[1] for call in steam.called("connect_p2p") if call[0] == GUEST]
    network.process([status(connection, ConnectionState.CONNECTED, GUEST)])
    return connection


def views(network):
    return {view.steam_id: view for view in network.members()}


def test_host_approves_correct_code():
    steam, network = host_network()
    connection = connect_guest(steam, network)
    assert views(network)[GUEST].status == "Checking access code"

    steam.inbox[connection] = [access.auth_message(CODE)]
    network.process([])

    assert network.approved == {GUEST}
    assert steam.sent[0] == (connection, access.ACCEPTED)
    assert steam.sent[1] == (connection, access.members_message([HOST, GUEST]))
    assert views(network)[GUEST].status == "Connected"
    assert network.status() == ("Connected to 1 of 1", "ok")


def test_host_denies_wrong_code():
    steam, network = host_network()
    connection = connect_guest(steam, network)

    steam.inbox[connection] = [access.auth_message("AAAAAAAAAA")]
    network.process([])

    assert network.approved == set()
    assert steam.sent == [(connection, access.DENIED)]
    assert steam.closed == [(connection, True)]
    assert views(network)[GUEST].status == "Access denied"


def test_host_does_not_reconnect_to_denied_member():
    steam, network = host_network()
    connection = connect_guest(steam, network)
    steam.inbox[connection] = [access.auth_message("AAAAAAAAAA")]
    network.process([])

    network.process([])
    network.process([])

    assert len(steam.called("connect_p2p")) == 1


def test_host_stops_checking_after_too_many_failures():
    steam, network = host_network(members=(GUEST, HOST))
    network._failures[GUEST] = MAX_FAILURES
    connection = connect_guest(steam, network)

    steam.inbox[connection] = [access.auth_message(CODE)]
    network.process([])

    assert network.approved == set()
    assert steam.sent == [(connection, access.DENIED)]


def test_host_denies_member_that_sends_no_code():
    clock = Clock()
    steam, network = host_network(clock=clock)
    connection = connect_guest(steam, network)

    clock.now = AUTH_TIMEOUT - 1
    network.process([])
    assert steam.closed == []

    clock.now = AUTH_TIMEOUT + 1
    network.process([])
    assert steam.closed == [(connection, True)]


def test_host_ignores_other_data_and_repeated_auth():
    steam, network = host_network()
    connection = connect_guest(steam, network)
    steam.inbox[connection] = [b"hello", access.auth_message(CODE), access.auth_message("XXXX")]

    network.process([])

    assert network.approved == {GUEST}
    assert [data for _, data in steam.sent].count(access.ACCEPTED) == 1
    assert access.DENIED not in [data for _, data in steam.sent]


def test_host_forgets_member_that_leaves():
    steam, network = host_network(members=(HOST, GUEST, OTHER))
    connection = connect_guest(steam, network)
    other = [call[1] for call in steam.called("connect_p2p") if call[0] == OTHER][0]
    network.process([status(other, ConnectionState.CONNECTED, OTHER)])
    steam.inbox[connection] = [access.auth_message(CODE)]
    steam.inbox[other] = [access.auth_message(CODE)]
    network.process([])
    steam.sent.clear()

    steam.members.remove(OTHER)
    network.process([member_update(OTHER, ChatMemberStateChange.LEFT)])

    assert network.approved == {GUEST}
    assert OTHER not in views(network)
    assert steam.sent == [(connection, access.members_message([HOST, GUEST]))]


def test_duplicate_member_updates_show_one_row():
    steam, network = host_network(members=(HOST,))
    steam.members.append(GUEST)

    network.process([member_update(GUEST)])
    network.process([member_update(GUEST), member_update(GUEST)])

    assert [view.steam_id for view in network.members()] == [HOST, GUEST]


def test_host_status_without_peers():
    steam, network = host_network(members=(HOST,))

    assert network.status() == ("Waiting for peers", "neutral")
    (me,) = network.members()
    assert (me.is_you, me.is_host, me.status) == (True, True, "Hosting")


def test_unknown_names_use_a_placeholder():
    steam, network = host_network(members=(HOST, STRANGER))

    assert views(network)[STRANGER].name == f"Steam user {str(STRANGER)[-4:]}"


def guest_network(code=CODE, clock=None):
    steam = FakeSteam(GUEST, [HOST, GUEST], names={HOST: "Host"})
    network = NetworkSession(steam, LOBBY, LISTEN_SOCKET, HOST, code, clock or Clock())
    return steam, network


def connect_to_host(steam, network, connection=7):
    network.process([incoming(connection, HOST)])
    network.process([status(connection, ConnectionState.CONNECTED, HOST, LISTEN_SOCKET)])
    return connection


def test_guest_sends_code_once_connected():
    steam, network = guest_network()
    network.process([])
    assert steam.sent == []
    assert network.status() == ("Connecting to host", "pending")

    connection = connect_to_host(steam, network)
    network.process([])

    assert steam.sent == [(connection, access.auth_message(CODE))]
    assert views(network)[GUEST].status == "Joining"


def test_guest_is_joined_after_host_accepts():
    steam, network = guest_network()
    connection = connect_to_host(steam, network)

    steam.inbox[connection] = [access.ACCEPTED, access.members_message([HOST, GUEST, OTHER])]
    network.process([])

    assert network.joined
    assert network.approved == {HOST, OTHER}
    assert network.status() == ("Connected", "ok")
    assert views(network)[HOST].status == "Connected"
    assert views(network)[GUEST].status == "Connected"


def test_guest_refused_by_host():
    steam, network = guest_network()
    connection = connect_to_host(steam, network)

    steam.inbox[connection] = [access.DENIED]
    network.process([])

    assert network.refused == "Incorrect access code"
    assert not network.joined


def test_guest_gives_up_when_host_does_not_answer():
    clock = Clock()
    steam, network = guest_network(clock=clock)
    connect_to_host(steam, network)

    clock.now = AUTH_TIMEOUT + 1
    network.process([])

    assert network.refused == "The host did not answer"


def test_guest_ignores_access_messages_from_other_members():
    steam = FakeSteam(GUEST, [HOST, GUEST, STRANGER])
    network = NetworkSession(steam, LOBBY, LISTEN_SOCKET, HOST, CODE, Clock())
    connect_to_host(steam, network)
    stranger = [call[1] for call in steam.called("connect_p2p") if call[0] == STRANGER][0]
    network.process([status(stranger, ConnectionState.CONNECTED, STRANGER)])

    steam.inbox[stranger] = [access.ACCEPTED, access.DENIED]
    network.process([])

    assert not network.joined
    assert not network.refused


def test_guest_sees_host_leave():
    steam, network = guest_network()
    connect_to_host(steam, network)

    steam.members.remove(HOST)
    network.process([member_update(HOST, ChatMemberStateChange.LEFT)])

    assert network.status() == ("Host left the network", "error")


def test_close_closes_connections():
    steam, network = host_network()
    connection = connect_guest(steam, network)

    network.close()

    assert steam.closed == [(connection, False)]


@pytest.mark.parametrize(
    "state", [ConnectionState.CLOSED_BY_PEER, ConnectionState.PROBLEM_DETECTED_LOCALLY]
)
def test_lost_connection(state):
    steam, network = guest_network()
    connection = connect_to_host(steam, network)
    steam.inbox[connection] = [access.ACCEPTED]
    network.process([])

    network.process([status(connection, state, HOST, LISTEN_SOCKET)])

    assert network.status() == ("Connection lost", "error")
    assert views(network)[HOST].status == "Connection lost"


def test_member_without_code_is_told_to_ask_for_one():
    steam, network = guest_network(code="")
    connection = connect_to_host(steam, network)
    network.process([])
    assert steam.sent == [(connection, access.auth_message(""))]

    steam.inbox[connection] = [access.DENIED]
    network.process([])

    assert network.refused == "Ask the host for an invite or the access code"


def test_host_denies_member_without_code():
    steam, network = host_network()
    connection = connect_guest(steam, network)

    steam.inbox[connection] = [access.auth_message("")]
    network.process([])

    assert network.approved == set()
    assert steam.sent == [(connection, access.DENIED)]
