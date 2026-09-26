"""The roster of members and addresses, and the rules for carrying packets between members."""

from ipaddress import IPv4Address

import pytest
from app_fakes import ipv4_packet

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


def test_destination_member():
    owners = {A2: B, A3: C}

    assert tunnel.destination_member(ipv4_packet(A1, A2), A1, owners) == B
    assert tunnel.destination_member(ipv4_packet(A1, A3), A1, owners) == C
    assert tunnel.destination_member(ipv4_packet(A1, A2), None, owners) is None
    assert tunnel.destination_member(ipv4_packet(A2, A3), A1, owners) is None


def test_accepts_packet():
    assert tunnel.accepts_packet(ipv4_packet(A1, A2), A1, A2)
    assert not tunnel.accepts_packet(ipv4_packet(A1, A2), None, A2)
    assert not tunnel.accepts_packet(ipv4_packet(A1, A2), A1, None)
    assert not tunnel.accepts_packet(ipv4_packet(A3, A2), A1, A2)
    assert not tunnel.accepts_packet(ipv4_packet(A1, A3), A1, A2)
