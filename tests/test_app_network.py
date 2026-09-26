"""NetworkSession seen from one member, with a FakeSteam.

test_app_coordination.py has whole networks of several members.
"""

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

from steamlan.app import access, roster, tunnel
from steamlan.app.network import (
    AUTH_TIMEOUT,
    HAND_OFF_INTERVAL,
    MAX_FAILURES,
    NO_MEMBERS_ONLINE,
    NetworkSession,
)
from steamlan.steam import ChatMemberStateChange, ConnectionState

CODE = "7K2QDM9XTE"
A1, A2, A3, A4 = (IPv4Address(f"10.77.0.{n}") for n in (1, 2, 3, 4))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def published(steam):
    return roster.decode(steam.lobby_metadata.get(roster.ROSTER_KEY, ""))


def views(network):
    return {view.steam_id: view for view in network.members()}


# The coordinator: HOST owns the lobby.


def coordinator(members=(HOST, GUEST), clock=None, known=None, code=CODE):
    steam = FakeSteam(HOST, members, names={GUEST: "Guest", OTHER: "Other"})
    network = NetworkSession(
        steam, LOBBY, LISTEN_SOCKET, code, clock or Clock(), known=known or {HOST: A1}
    )
    return steam, network


def connect(steam, network, steam_id):
    """HOST has the lowest SteamID, so it connects to everyone else."""
    network.process([])
    connection = [call[1] for call in steam.called("connect_p2p") if call[0] == steam_id][-1]
    network.process([status(connection, ConnectionState.CONNECTED, steam_id)])
    return connection


def admit(steam, network, steam_id, code=CODE, preferred=None):
    connection = connect(steam, network, steam_id)
    steam.inbox[connection] = [access.auth_message(code, preferred)]
    network.process([])
    return connection


def test_creator_is_the_first_member_and_publishes_the_roster():
    steam, network = coordinator(members=(HOST,))

    network.process([])

    assert network.joined and network.is_coordinator
    assert network.local_address == A1
    assert published(steam) == {HOST: A1}
    assert network.status() == ("Online", "ok")
    (me,) = network.members()
    assert (me.is_you, me.online, me.status, me.address) == (True, True, "Online", "10.77.0.1")


def test_coordinator_admits_correct_code_and_shares_it():
    steam, network = coordinator()
    connection = connect(steam, network, GUEST)
    assert views(network)[GUEST].status == "Joining"

    steam.inbox[connection] = [access.auth_message(CODE)]
    network.process([])

    assert network.roster == {HOST: A1, GUEST: A2}
    assert published(steam) == {HOST: A1, GUEST: A2}
    assert steam.sent == [(connection, access.accepted_message(CODE))]
    guest = views(network)[GUEST]
    assert (guest.status, guest.online, guest.address) == ("Online", True, "10.77.0.2")


def test_coordinator_denies_wrong_code():
    steam, network = coordinator()
    connection = admit(steam, network, GUEST, code="AAAAAAAAAA")

    assert network.roster == {HOST: A1}
    assert steam.sent == [(connection, access.DENIED)]
    assert steam.closed == [(connection, True)]
    assert GUEST not in views(network)


def test_coordinator_does_not_reconnect_to_denied_member():
    steam, network = coordinator()
    admit(steam, network, GUEST, code="AAAAAAAAAA")

    network.process([])
    network.process([])

    assert len(steam.called("connect_p2p")) == 1


def test_coordinator_stops_checking_after_too_many_failures():
    steam, network = coordinator()
    network._failures[GUEST] = MAX_FAILURES

    connection = admit(steam, network, GUEST)

    assert network.roster == {HOST: A1}
    assert steam.sent == [(connection, access.DENIED)]


def test_coordinator_denies_member_that_sends_no_code():
    clock = Clock()
    steam, network = coordinator(clock=clock)
    connection = connect(steam, network, GUEST)

    clock.now = AUTH_TIMEOUT - 1
    network.process([])
    assert steam.closed == []

    clock.now = AUTH_TIMEOUT + 1
    network.process([])
    assert steam.closed == [(connection, True)]


def test_coordinator_without_a_code_admits_nobody_new():
    steam, network = coordinator(code="")

    connection = admit(steam, network, GUEST, code="")

    assert network.roster == {HOST: A1}
    assert steam.sent == [(connection, access.DENIED)]


def test_coordinator_ignores_other_data():
    steam, network = coordinator()
    connection = connect(steam, network, GUEST)
    steam.inbox[connection] = [b"hello", b"SVL1 AUTH " + CODE.encode()]

    network.process([])

    assert network.roster == {HOST: A1}
    assert steam.sent == []


def test_addresses_follow_the_order_members_are_admitted():
    steam, network = coordinator(members=(HOST, GUEST, OTHER))

    admit(steam, network, OTHER)
    admit(steam, network, GUEST)

    assert network.roster == {HOST: A1, OTHER: A2, GUEST: A3}
    assert [view.address for view in network.members()] == ["10.77.0.1", "10.77.0.2", "10.77.0.3"]


def test_member_going_offline_keeps_its_address():
    steam, network = coordinator(members=(HOST, GUEST, OTHER))
    admit(steam, network, GUEST)

    steam.members.remove(GUEST)
    network.process([member_update(GUEST, ChatMemberStateChange.LEFT)])
    admit(steam, network, OTHER)

    assert network.roster == {HOST: A1, GUEST: A2, OTHER: A3}
    assert published(steam) == {HOST: A1, GUEST: A2, OTHER: A3}
    guest = views(network)[GUEST]
    assert (guest.online, guest.status, guest.tone, guest.address) == (
        False,
        "Offline",
        "neutral",
        "10.77.0.2",
    )


def test_returning_member_is_admitted_by_its_steam_id_with_its_old_address():
    steam, network = coordinator(members=(HOST,), known={HOST: A1, GUEST: A2})
    network.process([])
    assert published(steam) == {HOST: A1, GUEST: A2}

    steam.members.append(GUEST)
    network.process([member_update(GUEST)])
    connection = admit(steam, network, GUEST, code="")

    assert network.roster == {HOST: A1, GUEST: A2}
    assert steam.sent == [(connection, access.accepted_message(CODE))]


def test_explicit_leave_frees_the_address():
    steam, network = coordinator(members=(HOST, GUEST, OTHER))
    guest = admit(steam, network, GUEST)
    admit(steam, network, OTHER)

    steam.inbox[guest] = [access.LEAVE]
    network.process([])

    assert network.roster == {HOST: A1, OTHER: A3}
    assert published(steam) == {HOST: A1, OTHER: A3}

    steam.members.remove(GUEST)
    steam.members.append(STRANGER)
    network.process([member_update(GUEST, ChatMemberStateChange.LEFT), member_update(STRANGER)])
    admit(steam, network, STRANGER)
    assert network.roster[STRANGER] == A2


def test_leave_from_someone_outside_the_roster_changes_nothing():
    steam, network = coordinator()
    connection = connect(steam, network, GUEST)

    steam.inbox[connection] = [access.LEAVE]
    network.process([])

    assert network.left == set()


def test_new_member_gets_its_preferred_address_when_free():
    steam, network = coordinator(members=(HOST, GUEST, OTHER))

    admit(steam, network, GUEST, preferred=A4)
    admit(steam, network, OTHER, preferred=A4)

    assert network.roster == {HOST: A1, GUEST: A4, OTHER: A2}


def test_network_full():
    steam, network = coordinator()
    network.roster.update(
        {steam_id: IPv4Address(f"10.77.0.{steam_id}") for steam_id in range(2, 255)}
    )

    connection = admit(steam, network, GUEST)

    assert GUEST not in network.roster
    assert steam.sent == [(connection, access.DENIED)]


def test_coordinator_leaving_takes_itself_out_first():
    steam, network = coordinator()
    connection = admit(steam, network, GUEST)
    steam.sent.clear()

    assert network.announce_leave()

    assert published(steam) == {GUEST: A2}
    assert steam.sent == [(connection, access.LEAVE)]


# A member: HOST owns the lobby, GUEST is this member.


def member(code=CODE, clock=None, known=None, preferred=None, members=(HOST, GUEST)):
    steam = FakeSteam(GUEST, members, names={HOST: "Host", OTHER: "Other"})
    network = NetworkSession(
        steam,
        LOBBY,
        LISTEN_SOCKET,
        code,
        clock or Clock(),
        known=known,
        preferred=preferred,
    )
    return steam, network


def connect_to_owner(steam, network, connection=7):
    network.process([incoming(connection, HOST)])
    network.process([status(connection, ConnectionState.CONNECTED, HOST, LISTEN_SOCKET)])
    return connection


def accepted(steam, network, addresses=None, connection=7):
    """The coordinator admits this member: it writes the roster, then answers."""
    connect_to_owner(steam, network, connection)
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode(addresses or {HOST: A1, GUEST: A2})
    steam.inbox[connection] = [access.accepted_message(CODE)]
    network.process([])
    steam.sent.clear()
    return connection


def test_member_sends_code_once_connected_to_the_coordinator():
    steam, network = member()
    network.process([])
    assert steam.sent == []
    assert network.status() == ("Connecting to the network...", "pending")

    connection = connect_to_owner(steam, network)
    network.process([])

    assert steam.sent == [(connection, access.auth_message(CODE))]
    assert views(network)[GUEST].status == "Joining"


def test_member_joins_with_the_roster_after_being_accepted():
    steam, network = member(code="")

    accepted(steam, network, {HOST: A1, OTHER: A2, GUEST: A3})

    assert network.joined
    assert network.local_address == A3
    assert network.access_code == CODE
    assert network.status() == ("Online", "ok")
    other = views(network)[OTHER]
    assert (other.online, other.status, other.address) == (False, "Offline", "10.77.0.2")


def test_member_ignores_the_roster_until_the_coordinator_accepted_it():
    steam, network = member()
    connect_to_owner(steam, network)
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A2})

    network.process([])

    assert not network.joined


def test_member_ignores_answers_from_members_that_do_not_coordinate():
    steam, network = member(members=(HOST, GUEST, STRANGER))
    connect_to_owner(steam, network)
    stranger = [call[1] for call in steam.called("connect_p2p") if call[0] == STRANGER][0]
    network.process([status(stranger, ConnectionState.CONNECTED, STRANGER)])
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({STRANGER: A1, GUEST: A2})

    steam.inbox[stranger] = [access.accepted_message(CODE), access.DENIED]
    network.process([])

    assert not network.joined
    assert not network.refused


def test_member_refused():
    steam, network = member()
    connection = connect_to_owner(steam, network)

    steam.inbox[connection] = [access.DENIED]
    network.process([])

    assert network.refused == "Incorrect access code"
    assert network.status() == ("Incorrect access code", "error")


def test_member_without_code_is_told_to_ask_for_one():
    steam, network = member(code="")
    connection = connect_to_owner(steam, network)
    network.process([])
    assert steam.sent == [(connection, access.auth_message(""))]

    steam.inbox[connection] = [access.DENIED]
    network.process([])

    assert network.refused == "Ask a member of the network for an invite or the access code"


def test_member_gives_up_when_the_coordinator_does_not_answer():
    clock = Clock()
    steam, network = member(clock=clock)
    connect_to_owner(steam, network)
    network.process([])

    clock.now = AUTH_TIMEOUT + 1
    network.process([])

    assert network.refused == "The network did not answer"


def test_member_asks_the_new_coordinator_when_coordination_moves():
    steam, network = member(members=(HOST, GUEST, OTHER))
    host = connect_to_owner(steam, network)
    other = [call[1] for call in steam.called("connect_p2p") if call[0] == OTHER][0]
    network.process([status(other, ConnectionState.CONNECTED, OTHER)])
    assert steam.sent == [(host, access.auth_message(CODE))]

    steam.owner = OTHER
    steam.members.remove(HOST)
    network.process([member_update(HOST, ChatMemberStateChange.DISCONNECTED)])

    assert steam.sent[-1] == (other, access.auth_message(CODE))
    assert network.coordinator_id == OTHER


def test_returning_member_takes_its_address_back_from_the_lobby():
    steam, network = member(known={HOST: A1, GUEST: A2})
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A2, OTHER: A3})

    network.process([])

    assert network.joined
    assert network.local_address == A2
    assert steam.sent == []


def test_returning_member_that_was_taken_out_asks_for_its_old_address():
    steam, network = member(known={HOST: A1, GUEST: A2}, preferred=A2)
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1})
    connection = connect_to_owner(steam, network)
    network.process([])

    assert not network.joined
    assert steam.sent == [(connection, access.auth_message(CODE, A2))]


def test_member_ignores_a_roster_that_moves_it():
    steam, network = member()
    accepted(steam, network)

    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A3})
    network.process([])
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1})
    network.process([])

    assert network.roster == {HOST: A1, GUEST: A2}


def test_member_ignores_a_roster_written_by_an_owner_it_does_not_trust():
    steam, network = member(members=(HOST, GUEST, STRANGER))
    accepted(steam, network)

    steam.owner = STRANGER
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A2, STRANGER: A3})
    network.process([])

    assert network.roster == {HOST: A1, GUEST: A2}
    assert network.coordinator_id == STRANGER


def test_member_takes_over_with_the_last_roster_when_steam_makes_it_the_owner():
    steam, network = member(members=(HOST, GUEST, OTHER))
    accepted(steam, network)
    # The coordinator admits OTHER and leaves; both arrive together.
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A2, OTHER: A3})
    steam.owner = GUEST
    steam.members.remove(HOST)
    network.process([member_update(HOST, ChatMemberStateChange.LEFT)])

    assert network.is_coordinator
    assert network.roster == {HOST: A1, GUEST: A2, OTHER: A3}
    assert published(steam) == {HOST: A1, GUEST: A2, OTHER: A3}


def test_owner_that_is_not_a_member_hands_the_network_on():
    clock = Clock()
    steam = FakeSteam(STRANGER, [OTHER, GUEST, STRANGER])
    steam.owner = STRANGER
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A2, OTHER: A3})
    network = NetworkSession(steam, LOBBY, LISTEN_SOCKET, CODE, clock)

    network.process([])
    network.process([])

    assert steam.called("set_lobby_owner") == [(LOBBY, GUEST)]
    assert not network.is_coordinator
    assert not network.refused


def test_owner_that_is_not_a_member_retries_the_hand_off():
    clock = Clock()
    steam = FakeSteam(STRANGER, [GUEST, STRANGER])
    steam.owner = STRANGER
    steam.fail.add("set_lobby_owner")
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({GUEST: A2})
    network = NetworkSession(steam, LOBBY, LISTEN_SOCKET, CODE, clock)

    network.process([])
    network.process([])
    assert steam.called("set_lobby_owner") == []
    steam.fail.clear()
    clock.now = HAND_OFF_INTERVAL + 1
    network.process([])

    assert steam.called("set_lobby_owner") == [(LOBBY, GUEST)]


def test_owner_that_is_not_a_member_with_nobody_to_hand_to():
    steam = FakeSteam(STRANGER, [STRANGER])
    steam.owner = STRANGER
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A2})
    network = NetworkSession(steam, LOBBY, LISTEN_SOCKET, CODE, Clock())

    network.process([])

    assert network.refused == NO_MEMBERS_ONLINE
    assert steam.called("set_lobby_owner") == []


def test_member_leaving_tells_the_coordinator():
    steam, network = member()
    connection = accepted(steam, network)

    assert network.announce_leave()

    assert steam.sent == [(connection, access.LEAVE)]


def test_member_leaving_waits_until_it_can_tell_the_coordinator():
    steam, network = member(known={HOST: A1, GUEST: A2})
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A2})
    network.process([])

    assert not network.announce_leave()
    connect_to_owner(steam, network)
    assert network.announce_leave()


def test_close_closes_connections():
    steam, network = coordinator()
    connection = connect(steam, network, GUEST)

    network.close(linger=True)

    assert steam.closed == [(connection, True)]


@pytest.mark.parametrize(
    "state", [ConnectionState.CLOSED_BY_PEER, ConnectionState.PROBLEM_DETECTED_LOCALLY]
)
def test_lost_connection_to_a_member_that_is_still_there(state):
    steam, network = member()
    connection = accepted(steam, network)

    network.process([status(connection, state, HOST, LISTEN_SOCKET)])

    host = views(network)[HOST]
    assert (host.status, host.online, host.latency) == ("Reconnecting...", True, "")
    assert network.status() == ("Online", "ok")


def test_member_that_drops_out_of_the_lobby_lost_its_connection():
    steam, network = coordinator(members=(HOST, GUEST, OTHER))
    admit(steam, network, GUEST)
    admit(steam, network, OTHER)

    steam.members.remove(GUEST)
    steam.members.remove(OTHER)
    network.process(
        [
            member_update(GUEST, ChatMemberStateChange.DISCONNECTED),
            member_update(OTHER, ChatMemberStateChange.LEFT),
        ]
    )

    assert views(network)[GUEST].status == "Lost connection"
    assert views(network)[OTHER].status == "Offline"
    assert not views(network)[GUEST].online
    assert network.roster == {HOST: A1, GUEST: A2, OTHER: A3}

    steam.members.append(GUEST)
    network.process([member_update(GUEST)])
    assert views(network)[GUEST].status == "Connecting..."


def test_unknown_names_use_a_placeholder_and_known_ones_are_remembered():
    steam, network = coordinator(members=(HOST, STRANGER, GUEST))
    admit(steam, network, GUEST)
    assert views(network)[STRANGER].name == f"Steam user {str(STRANGER)[-4:]}"
    assert views(network)[GUEST].name == "Guest"

    del steam.names[GUEST]
    steam.members.remove(GUEST)
    network.process([member_update(GUEST, ChatMemberStateChange.LEFT)])

    assert views(network)[GUEST].name == "Guest"
    assert network.names[GUEST] == "Guest"


def test_member_list_has_no_special_roles():
    steam, network = member()
    accepted(steam, network)

    assert [(v.name, v.is_you) for v in network.members()] == [("Me", True), ("Host", False)]
    assert not hasattr(network.members()[0], "is_host")


# Packets


def test_coordinator_sends_packets_to_the_member_that_owns_the_destination():
    steam, network = coordinator(members=(HOST, GUEST, OTHER))
    guest = admit(steam, network, GUEST)
    other = admit(steam, network, OTHER)
    to_guest = ipv4_packet(A1, A2)
    to_other = ipv4_packet(A1, A3, b"x" * 1400)

    assert network.send_packet(to_guest)
    assert network.send_packet(to_other)

    assert steam.packets == [
        (guest, tunnel.PACKET_PREFIX + to_guest),
        (other, tunnel.PACKET_PREFIX + to_other),
    ]


@pytest.mark.parametrize(
    "packet",
    [
        ipv4_packet(A1, A4),  # nobody has this address
        ipv4_packet(A1, "10.77.0.0"),  # the network's own address
        ipv4_packet(A1, "192.168.1.1"),  # outside the network
        ipv4_packet(A1, A1),  # to itself
        ipv4_packet(A3, A2),  # not from this member's address
        ipv4_packet(A3, "10.77.0.255"),  # broadcast, not from this member's address
        ipv4_packet("0.0.0.0", "255.255.255.255"),  # e.g. DHCP
        ipv4_packet(A2, "239.255.255.250"),  # multicast, not from this member
        ipv4_packet(A1, "224.0.0.22", protocol=2),  # IGMP
        ipv4_packet(A1, A2)[:19],  # truncated
        b"\x60" + bytes(39),  # IPv6
        b"",
    ],
    ids=[
        "unknown",
        "network address",
        "outside",
        "self",
        "foreign source",
        "foreign broadcast",
        "unconfigured source",
        "foreign multicast",
        "igmp",
        "truncated",
        "ipv6",
        "empty",
    ],
)
def test_packets_that_cannot_be_routed_are_dropped(packet):
    steam, network = coordinator()
    admit(steam, network, GUEST)

    assert not network.send_packet(packet)
    assert steam.packets == []
    assert network.dropped_packets == 1


def test_packets_go_only_to_members():
    steam, network = coordinator()
    connect(steam, network, GUEST)

    assert not network.send_packet(ipv4_packet(A1, A2))
    assert steam.packets == []


def test_packets_for_an_offline_member_are_dropped():
    steam, network = coordinator()
    admit(steam, network, GUEST)
    steam.members.remove(GUEST)
    network.process([member_update(GUEST, ChatMemberStateChange.LEFT)])

    assert not network.send_packet(ipv4_packet(A1, A2))
    assert network.dropped_packets == 1


def test_member_cannot_send_before_it_has_an_address():
    steam, network = member()
    connect_to_owner(steam, network)

    assert not network.send_packet(ipv4_packet(A2, A1))
    assert steam.packets == []


def test_members_send_to_each_other_directly_not_through_the_coordinator():
    steam, network = member(members=(HOST, GUEST, OTHER))
    host = connect_to_owner(steam, network)
    (other,) = [call[1] for call in steam.called("connect_p2p") if call[0] == OTHER]
    network.process([status(other, ConnectionState.CONNECTED, OTHER)])
    accepted(steam, network, {HOST: A1, GUEST: A2, OTHER: A3}, connection=host)

    assert network.send_packet(ipv4_packet(A2, A3))
    assert network.send_packet(ipv4_packet(A2, A1))

    assert [connection for connection, _ in steam.packets] == [other, host]


def test_packets_are_not_sent_while_the_connection_is_down():
    steam, network = coordinator()
    guest = admit(steam, network, GUEST)
    network.process([status(guest, ConnectionState.CLOSED_BY_PEER, GUEST)])

    assert not network.send_packet(ipv4_packet(A1, A2))


def test_member_accepts_packets_from_their_owner():
    steam, network = member()
    connection = accepted(steam, network)
    packet = ipv4_packet(A1, A2)

    steam.inbox[connection] = [tunnel.packet_message(packet)]
    network.process([])

    assert network.take_packets() == [packet]
    assert network.take_packets() == []


@pytest.mark.parametrize(
    "packet",
    [
        ipv4_packet(A3, A2),  # pretending to be another member
        ipv4_packet(A3, "10.77.0.255"),  # a broadcast pretending the same
        ipv4_packet(A3, "239.255.255.250"),  # a multicast pretending the same
        ipv4_packet(A2, "255.255.255.255"),  # pretending to be this member
        ipv4_packet(A1, A3),  # addressed to someone else
        ipv4_packet(A1, "10.77.0.0"),
        ipv4_packet(A1, "224.0.0.22", protocol=2),  # IGMP
        ipv4_packet("192.168.1.1", A2),
        ipv4_packet(A1, A2)[:10],
        b"\x60" + bytes(39),
    ],
    ids=[
        "spoofed source",
        "spoofed broadcast",
        "spoofed multicast",
        "own source",
        "other destination",
        "network address",
        "igmp",
        "outside",
        "truncated",
        "ipv6",
    ],
)
def test_member_drops_packets_that_do_not_fit_their_sender(packet):
    steam, network = member()
    connection = accepted(steam, network, {HOST: A1, GUEST: A2, OTHER: A3})

    steam.inbox[connection] = [tunnel.packet_message(packet)]
    network.process([])

    assert network.take_packets() == []
    assert network.dropped_packets == 1


def test_coordinator_drops_packets_from_members_it_has_not_admitted():
    steam, network = coordinator()
    connection = connect(steam, network, GUEST)

    steam.inbox[connection] = [tunnel.packet_message(ipv4_packet(A2, A1))]
    network.process([])

    assert network.take_packets() == []


def test_packets_and_control_messages_never_mix():
    steam, network = member()
    connection = connect_to_owner(steam, network)

    # A packet whose bytes spell a control message is still only a packet, and
    # isn't even delivered: the member has no address yet.
    steam.inbox[connection] = [tunnel.packet_message(access.accepted_message(CODE))]
    network.process([])
    assert network.access_code == CODE and network.take_packets() == []
    assert network.coordinator_id not in network._trusted

    # A control message is never taken for a packet.
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A2})
    steam.inbox[connection] = [access.accepted_message(CODE)]
    network.process([])
    assert network.joined
    assert network.take_packets() == []


# Broadcast and multicast (test_app_broadcast.py has whole networks)

BROADCASTS = ["10.77.0.255", "255.255.255.255", "239.255.255.250"]


@pytest.mark.parametrize("destination", BROADCASTS)
def test_flooding_skips_members_not_admitted_or_not_connected(destination):
    steam, network = coordinator(members=(HOST, GUEST, OTHER, STRANGER))
    guest = admit(steam, network, GUEST)
    # OTHER is connected but hasn't shown a code; STRANGER isn't connected.
    connect(steam, network, OTHER)
    packet = ipv4_packet(A1, destination, b"discover", protocol=17)

    assert network.send_packet(packet) == 1

    assert steam.packets == [(guest, tunnel.packet_message(packet))]
    assert network.traffic.snapshot()["tx_copies"] == 1


def test_flooding_with_nobody_connected_is_counted_as_dropped():
    steam, network = coordinator(members=(HOST,))

    assert network.send_packet(ipv4_packet(A1, "10.77.0.255")) == 0

    assert network.traffic.snapshot()["dropped"] == {"tx_no_recipients": 1}


@pytest.mark.parametrize("destination", BROADCASTS)
def test_received_broadcast_goes_to_windows_and_nowhere_else(destination):
    steam, network = member(members=(HOST, GUEST, OTHER))
    host = connect_to_owner(steam, network)
    other = [c[1] for c in steam.called("connect_p2p") if c[0] == OTHER][0]
    network.process([status(other, ConnectionState.CONNECTED, OTHER)])
    accepted(steam, network, {HOST: A1, GUEST: A2, OTHER: A3}, connection=host)
    packet = ipv4_packet(A1, destination, b"discover", protocol=17)
    steam.unreliable.clear()

    steam.inbox[host] = [tunnel.packet_message(packet)]
    network.process([])

    assert network.take_packets() == [packet]
    # Not a single attempt to send it on, not even one the rules would stop.
    assert steam.packets == []
    snapshot = network.traffic.snapshot()
    assert sum(snapshot["tx"].values()) == 0 and snapshot["dropped"] == {}


@pytest.mark.parametrize("destination", BROADCASTS)
def test_broadcast_from_someone_not_admitted_is_rejected(destination):
    steam, network = coordinator()
    connection = connect(steam, network, GUEST)

    steam.inbox[connection] = [tunnel.packet_message(ipv4_packet(A2, destination))]
    network.process([])

    assert network.take_packets() == []
    assert network.traffic.snapshot()["dropped"] == {"rx_rejected": 1}


def test_ping_inside_a_packet_is_not_answered_and_never_reaches_windows():
    steam, network = member()
    connection = accepted(steam, network)
    steam.unreliable.clear()

    steam.inbox[connection] = [tunnel.packet_message(access.ping_message(5))]
    network.process([])
    assert network.take_packets() == []
    assert (connection, access.pong_message(5)) not in steam.unreliable
    assert network.traffic.snapshot()["dropped"] == {"rx_rejected": 1}

    # A real PING is answered and never reaches Windows either.
    steam.inbox[connection] = [access.ping_message(6)]
    network.process([])
    assert network.take_packets() == []
    assert (connection, access.pong_message(6)) in steam.unreliable
