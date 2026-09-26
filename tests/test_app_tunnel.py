"""The roster of members and addresses, and the rules for carrying packets between members."""

from ipaddress import IPv4Address

import pytest
from app_fakes import ipv4_packet

from steamlan.adapter.ipv4 import Delivery, addressing
from steamlan.app import access, roster, tunnel

HOST, B, C, D = 1, 2, 3, 4
A1, A2, A3 = (IPv4Address(f"10.77.0.{n}") for n in (1, 2, 3))


def test_addresses_are_given_lowest_first_and_kept():
    members = {}

    assert roster.assign(members, HOST) == A1
    assert roster.assign(members, B) == A2
    assert roster.assign(members, C) == A3
    assert roster.assign(members, B) == A2
    assert members == {HOST: A1, B: A2, C: A3}


def test_freed_addresses_are_given_out_again_lowest_first():
    members = {HOST: A1, B: A2, C: A3}

    del members[B]
    del members[HOST]

    assert roster.assign(members, D) == A1


def test_preferred_address_when_free():
    members = {HOST: A1}

    assert roster.assign(members, B, IPv4Address("10.77.0.9")) == IPv4Address("10.77.0.9")
    assert roster.assign(members, C, IPv4Address("10.77.0.9")) == A2
    assert roster.assign(members, D, IPv4Address("10.77.0.255")) == A3


def test_roster_runs_out_after_254_members():
    members = {}
    for steam_id in range(10, 10 + 254):
        roster.assign(members, steam_id)

    assert members[263] == IPv4Address("10.77.0.254")
    with pytest.raises(ValueError, match="no free address"):
        roster.assign(members, 999)


def test_roster_round_trips_through_lobby_metadata():
    members = {C: A3, HOST: A1}

    assert roster.encode(members) == "1=10.77.0.1,3=10.77.0.3"
    assert roster.decode(roster.encode(members)) == members
    assert roster.decode("") == {}


def test_roster_of_every_address_fits_lobby_metadata():
    members = {76561197960265729 + n: IPv4Address(f"10.77.0.{n + 1}") for n in range(254)}

    # Steam keeps lobby metadata values up to k_cubChatMetadataMax, 8192 bytes.
    assert len(roster.encode(members)) < 8192


@pytest.mark.parametrize(
    "text",
    [
        "1,x",
        "-5=10.77.0.2",
        f"{2**64}=10.77.0.2",
        "0=10.77.0.2",
        "5",
        "5=10.77.0.x",
        "5=192.168.1.2",
        "5=10.77.0.0",
        "5=10.77.0.255",
        "5=10.77.0.2,5=10.77.0.3",
        "5=10.77.0.2,6=10.77.0.2",
    ],
)
def test_malformed_rosters(text):
    assert roster.decode(text) is None


def test_packet_messages_are_told_apart_by_their_first_byte():
    packet = ipv4_packet(A1, A2)

    assert tunnel.packet_message(packet) == b"\x00" + packet
    assert tunnel.packet_payload(tunnel.packet_message(packet)) == packet
    assert tunnel.packet_payload(tunnel.packet_message(b"")) == b""
    accepted = access.accepted_message("7K2QDM9XTE")
    for control in (accepted, access.DENIED, access.LEAVE, access.auth_message("X"), b"", b"\x45"):
        assert tunnel.packet_payload(control) is None
    assert access.parse_message(tunnel.packet_message(accepted)) is None


@pytest.mark.parametrize(
    ("destination", "delivery"),
    [
        (A2, Delivery.UNICAST),
        ("10.77.0.254", Delivery.UNICAST),
        ("10.77.0.255", Delivery.BROADCAST),
        ("255.255.255.255", Delivery.BROADCAST),
        ("224.0.0.1", Delivery.MULTICAST),
        ("239.255.255.250", Delivery.MULTICAST),
        ("239.255.255.255", Delivery.MULTICAST),
        ("10.77.0.0", None),
        ("10.77.1.1", None),
        ("192.168.1.255", None),
        ("223.255.255.255", None),
        ("240.0.0.1", None),
        ("0.0.0.0", None),
    ],
)
def test_packets_are_classified_by_destination(destination, delivery):
    info = addressing(ipv4_packet(A1, destination, protocol=17))

    assert (info.source, info.destination, info.protocol) == (A1, IPv4Address(destination), 17)
    assert info.delivery is delivery


def test_only_ipv4_is_classified():
    assert addressing(b"\x60" + bytes(39)) is None
    assert addressing(ipv4_packet(A1, A2)[:19]) is None
    assert addressing(b"") is None


def test_outgoing():
    assert tunnel.outgoing(ipv4_packet(A1, A2), A1).delivery is Delivery.UNICAST
    assert tunnel.outgoing(ipv4_packet(A1, "10.77.0.255"), A1).delivery is Delivery.BROADCAST
    assert tunnel.outgoing(ipv4_packet(A1, "255.255.255.255"), A1).delivery is Delivery.BROADCAST
    assert tunnel.outgoing(ipv4_packet(A1, "239.1.2.3"), A1).delivery is Delivery.MULTICAST
    assert tunnel.outgoing(ipv4_packet(A1, A2), None) is None
    assert tunnel.outgoing(ipv4_packet(A2, A3), A1) is None
    assert tunnel.outgoing(ipv4_packet(A2, "10.77.0.255"), A1) is None
    assert tunnel.outgoing(ipv4_packet(A1, A1), A1) is None
    assert tunnel.outgoing(ipv4_packet(A1, "224.0.0.22", protocol=2), A1) is None
    assert tunnel.outgoing(ipv4_packet(A1, "8.8.8.8"), A1) is None


def test_accepts_packet():
    assert tunnel.accepts_packet(ipv4_packet(A1, A2), A1, A2) is Delivery.UNICAST
    assert tunnel.accepts_packet(ipv4_packet(A1, "10.77.0.255"), A1, A2) is Delivery.BROADCAST
    assert tunnel.accepts_packet(ipv4_packet(A1, "255.255.255.255"), A1, A2) is Delivery.BROADCAST
    assert tunnel.accepts_packet(ipv4_packet(A1, "239.1.2.3"), A1, A2) is Delivery.MULTICAST
    assert not tunnel.accepts_packet(ipv4_packet(A3, "10.77.0.255"), A1, A2)
    assert not tunnel.accepts_packet(ipv4_packet(A1, "10.77.0.255"), None, A2)
    assert not tunnel.accepts_packet(ipv4_packet(A2, "239.1.2.3"), A2, A2)
    assert not tunnel.accepts_packet(ipv4_packet(A1, A2), None, A2)
    assert not tunnel.accepts_packet(ipv4_packet(A1, A2), A1, None)
    assert not tunnel.accepts_packet(ipv4_packet(A3, A2), A1, A2)
    assert not tunnel.accepts_packet(ipv4_packet(A1, A3), A1, A2)
