from ipaddress import IPv4Address

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
    ipv4_packet,
    member_update,
    status,
)

from steamlan.app import access, tunnel
from steamlan.app.network import AUTH_TIMEOUT, MAX_FAILURES, NetworkSession
from steamlan.steam import ChatMemberStateChange, ConnectionState

CODE = "7K2QDM9XTE"
A1 = IPv4Address("10.77.0.1")
A2 = IPv4Address("10.77.0.2")
A3 = IPv4Address("10.77.0.3")
A4 = IPv4Address("10.77.0.4")


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
    assert steam.sent[1] == (connection, access.members_message({HOST: A1, GUEST: A2}))
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
    assert steam.sent == [(connection, access.members_message({HOST: A1, GUEST: A2}))]


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

    steam.inbox[connection] = [
        access.ACCEPTED,
        access.members_message({HOST: A1, GUEST: A2, OTHER: A3}),
    ]
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


def admit(steam, network, steam_id):
    """Connect a member to the host and let it present the right code."""
    network.process([])
    (connection,) = [call[1] for call in steam.called("connect_p2p") if call[0] == steam_id]
    network.process([status(connection, ConnectionState.CONNECTED, steam_id)])
    steam.inbox[connection] = [access.auth_message(CODE)]
    network.process([])
    return connection


def test_host_is_always_the_first_address():
    steam, network = host_network(members=(HOST,))

    assert network.local_address == A1
    assert network.addresses == {HOST: A1}
    assert views(network)[HOST].address == "10.77.0.1"


def test_host_assigns_addresses_in_the_order_members_are_admitted():
    steam, network = host_network(members=(HOST, GUEST, OTHER))

    assert views(network)[GUEST].address == ""
    other = admit(steam, network, OTHER)
    guest = admit(steam, network, GUEST)

    assert network.addresses == {HOST: A1, OTHER: A2, GUEST: A3}
    assert views(network)[OTHER].address == "10.77.0.2"
    assert views(network)[GUEST].address == "10.77.0.3"
    # Everyone admitted gets the same, complete mapping.
    everyone = access.members_message({HOST: A1, OTHER: A2, GUEST: A3})
    assert (other, everyone) in steam.sent
    assert (guest, everyone) in steam.sent


def test_member_that_is_not_admitted_gets_no_address():
    steam, network = host_network()
    connect_guest(steam, network)

    assert network.addresses == {HOST: A1}
    assert views(network)[GUEST].address == ""


def test_address_of_a_member_that_left_is_given_out_again():
    steam, network = host_network(members=(HOST, GUEST, OTHER))
    admit(steam, network, GUEST)
    other = admit(steam, network, OTHER)
    steam.sent.clear()

    steam.members.remove(GUEST)
    network.process([member_update(GUEST, ChatMemberStateChange.LEFT)])

    assert network.addresses == {HOST: A1, OTHER: A3}
    assert steam.sent == [(other, access.members_message({HOST: A1, OTHER: A3}))]

    steam.members.append(STRANGER)
    network.process([member_update(STRANGER)])
    admit(steam, network, STRANGER)
    assert network.addresses == {HOST: A1, OTHER: A3, STRANGER: A2}


def admitted_guest(addresses=None):
    steam, network = guest_network()
    connection = connect_to_host(steam, network)
    steam.inbox[connection] = [
        access.ACCEPTED,
        access.members_message(addresses or {HOST: A1, GUEST: A2}),
    ]
    network.process([])
    steam.sent.clear()
    return steam, network, connection


def test_guest_learns_every_address_from_the_host():
    steam, network, _ = admitted_guest({HOST: A1, OTHER: A2, GUEST: A3})

    assert network.local_address == A3
    assert network.addresses == {HOST: A1, OTHER: A2, GUEST: A3}
    assert views(network)[HOST].address == "10.77.0.1"
    assert views(network)[GUEST].address == "10.77.0.3"


def test_guest_has_no_address_until_the_host_assigns_one():
    steam, network = guest_network()
    connect_to_host(steam, network)

    assert network.local_address is None
    assert views(network)[GUEST].address == ""


def test_guest_ignores_address_lists_from_other_members():
    steam = FakeSteam(GUEST, [HOST, GUEST, STRANGER])
    network = NetworkSession(steam, LOBBY, LISTEN_SOCKET, HOST, CODE, Clock())
    connect_to_host(steam, network)
    stranger = [call[1] for call in steam.called("connect_p2p") if call[0] == STRANGER][0]
    network.process([status(stranger, ConnectionState.CONNECTED, STRANGER)])

    steam.inbox[stranger] = [access.members_message({HOST: A1, GUEST: A2, STRANGER: A3})]
    network.process([])

    assert network.addresses == {}
    assert network.local_address is None


@pytest.mark.parametrize(
    "addresses",
    [
        {GUEST: A2, OTHER: A1},  # without the host
        {HOST: A1, GUEST: A3},  # moves this member to another address
    ],
    ids=["no host", "own address changed"],
)
def test_guest_ignores_address_lists_that_do_not_fit(addresses):
    steam, network, connection = admitted_guest()

    steam.inbox[connection] = [access.members_message(addresses)]
    network.process([])

    assert network.addresses == {HOST: A1, GUEST: A2}


def test_host_sends_packet_to_the_member_that_owns_the_destination():
    steam, network = host_network(members=(HOST, GUEST, OTHER))
    guest = admit(steam, network, GUEST)
    other = admit(steam, network, OTHER)
    to_guest = ipv4_packet(A1, A2)
    to_other = ipv4_packet(A1, A3, b"x" * 1400)

    assert network.send_packet(to_guest)
    assert network.send_packet(to_other)

    assert steam.unreliable == [
        (guest, tunnel.PACKET_PREFIX + to_guest),
        (other, tunnel.PACKET_PREFIX + to_other),
    ]


@pytest.mark.parametrize(
    "packet",
    [
        ipv4_packet(A1, A4),  # nobody has this address
        ipv4_packet(A1, "10.77.0.255"),  # broadcast
        ipv4_packet(A1, "224.0.0.251"),  # multicast
        ipv4_packet(A1, "192.168.1.1"),  # outside the network
        ipv4_packet(A1, A1),  # to itself
        ipv4_packet(A3, A2),  # not from this member's address
        ipv4_packet(A1, A2)[:19],  # truncated
        b"\x60" + bytes(39),  # IPv6
        b"",
    ],
    ids=[
        "unknown",
        "broadcast",
        "multicast",
        "outside",
        "self",
        "foreign source",
        "truncated",
        "ipv6",
        "empty",
    ],
)
def test_host_drops_packets_it_cannot_route(packet):
    steam, network = host_network()
    admit(steam, network, GUEST)

    assert not network.send_packet(packet)
    assert steam.unreliable == []
    assert network.dropped_packets == 1


def test_packets_go_only_to_admitted_members():
    steam, network = host_network()
    connect_guest(steam, network)

    assert not network.send_packet(ipv4_packet(A1, A2))
    assert steam.unreliable == []


def test_guest_cannot_send_before_it_has_an_address():
    steam, network = guest_network()
    connect_to_host(steam, network)

    assert not network.send_packet(ipv4_packet(A2, A1))
    assert steam.unreliable == []


def test_guest_sends_to_other_members_directly():
    steam = FakeSteam(GUEST, [HOST, GUEST, OTHER])
    network = NetworkSession(steam, LOBBY, LISTEN_SOCKET, HOST, CODE, Clock())
    host = connect_to_host(steam, network)
    (other,) = [call[1] for call in steam.called("connect_p2p") if call[0] == OTHER]
    network.process([status(other, ConnectionState.CONNECTED, OTHER)])
    steam.inbox[host] = [access.ACCEPTED, access.members_message({HOST: A1, GUEST: A2, OTHER: A3})]
    network.process([])

    assert network.send_packet(ipv4_packet(A2, A3))
    assert network.send_packet(ipv4_packet(A2, A1))

    assert [connection for connection, _ in steam.unreliable] == [other, host]


def test_packets_are_not_sent_while_the_connection_is_down():
    steam, network = host_network()
    guest = admit(steam, network, GUEST)
    network.process([status(guest, ConnectionState.CLOSED_BY_PEER, GUEST)])

    assert not network.send_packet(ipv4_packet(A1, A2))


def test_guest_accepts_packets_from_their_owner():
    steam, network, connection = admitted_guest()
    packet = ipv4_packet(A1, A2)

    steam.inbox[connection] = [tunnel.packet_message(packet)]
    network.process([])

    assert network.take_packets() == [packet]
    assert network.take_packets() == []


@pytest.mark.parametrize(
    "packet",
    [
        ipv4_packet(A3, A2),  # the host pretending to be another member
        ipv4_packet(A1, A3),  # addressed to someone else
        ipv4_packet(A1, "10.77.0.255"),
        ipv4_packet("192.168.1.1", A2),
        ipv4_packet(A1, A2)[:10],
        b"\x60" + bytes(39),
    ],
    ids=["spoofed source", "other destination", "broadcast", "outside", "truncated", "ipv6"],
)
def test_guest_drops_packets_that_do_not_fit_their_sender(packet):
    steam, network, connection = admitted_guest({HOST: A1, GUEST: A2, OTHER: A3})

    steam.inbox[connection] = [tunnel.packet_message(packet)]
    network.process([])

    assert network.take_packets() == []
    assert network.dropped_packets == 1


def test_host_drops_packets_from_members_it_has_not_admitted():
    steam, network = host_network()
    connection = connect_guest(steam, network)

    steam.inbox[connection] = [tunnel.packet_message(ipv4_packet(A2, A1))]
    network.process([])

    assert network.take_packets() == []


def test_packets_and_control_messages_never_mix():
    steam, network = guest_network()
    connection = connect_to_host(steam, network)

    # A packet whose bytes spell a control message is still only a packet, and
    # isn't even delivered: the guest has no address yet.
    steam.inbox[connection] = [tunnel.packet_message(access.ACCEPTED)]
    network.process([])
    assert not network.joined
    assert network.take_packets() == []

    # A control message is never taken for a packet.
    steam.inbox[connection] = [access.ACCEPTED, access.members_message({HOST: A1, GUEST: A2})]
    network.process([])
    assert network.joined
    assert network.take_packets() == []


class Wire:
    """Carries the messages two FakeSteams send each other over one connection."""

    def __init__(self, a, a_connection, b, b_connection):
        self.ends = [(a, a_connection, b, b_connection), (b, b_connection, a, a_connection)]

    def deliver(self):
        for source, source_connection, target, target_connection in self.ends:
            for kind in ("sent", "unreliable"):
                queue = getattr(source, kind)
                for connection, data in list(queue):
                    if connection == source_connection:
                        target.inbox.setdefault(target_connection, []).append(data)
                        queue.remove((connection, data))


def test_packets_cross_between_host_and_member():
    """PC A 10.77.0.1 pings PC B 10.77.0.2, and B's reply comes back, as bytes."""
    host_steam, host = host_network()
    guest_steam, guest = guest_network()
    host.process([])
    (host_side,) = [c[1] for c in host_steam.called("connect_p2p") if c[0] == GUEST]
    guest.process([incoming(9, HOST)])
    host.process([status(host_side, ConnectionState.CONNECTED, GUEST)])
    guest.process([status(9, ConnectionState.CONNECTED, HOST, LISTEN_SOCKET)])
    wire = Wire(host_steam, host_side, guest_steam, 9)
    for _ in range(3):
        guest.process([])
        wire.deliver()
        host.process([])
        wire.deliver()

    assert guest.joined
    assert (host.local_address, guest.local_address) == (A1, A2)
    assert host.addresses == guest.addresses == {HOST: A1, GUEST: A2}

    request = ipv4_packet(A1, A2, b"echo request")
    assert host.send_packet(request)
    wire.deliver()
    guest.process([])
    assert guest.take_packets() == [request]

    reply = ipv4_packet(A2, A1, b"echo reply")
    assert guest.send_packet(reply)
    wire.deliver()
    host.process([])
    assert host.take_packets() == [reply]
