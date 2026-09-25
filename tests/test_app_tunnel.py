"""Virtual IP assignment and the rules for carrying packets between members."""

from ipaddress import IPv4Address

import pytest
from app_fakes import ipv4_packet

from steamlan.app import access, tunnel
from steamlan.app.addresses import AddressPool

HOST, B, C, D = 1, 2, 3, 4
A1, A2, A3 = (IPv4Address(f"10.77.0.{n}") for n in (1, 2, 3))


def test_host_is_first_and_members_follow_in_order():
    pool = AddressPool(HOST)

    assert pool.assigned == {HOST: A1}
    assert pool.assign(B) == A2
    assert pool.assign(C) == A3
    assert pool.assign(B) == A2


def test_released_addresses_are_reused_lowest_first():
    pool = AddressPool(HOST)
    pool.assign(B)
    pool.assign(C)

    pool.release(B)
    pool.release(HOST)

    assert pool.assign(D) == A2
    assert pool.assigned[HOST] == A1


def test_pool_runs_out_after_253_members():
    pool = AddressPool(HOST)
    for steam_id in range(10, 10 + 253):
        pool.assign(steam_id)

    assert pool.assigned[262] == IPv4Address("10.77.0.254")
    with pytest.raises(ValueError, match="no free address"):
        pool.assign(999)


def test_packet_messages_are_told_apart_by_their_first_byte():
    packet = ipv4_packet(A1, A2)

    assert tunnel.packet_message(packet) == b"\x00" + packet
    assert tunnel.packet_payload(tunnel.packet_message(packet)) == packet
    assert tunnel.packet_payload(tunnel.packet_message(b"")) == b""
    for control in (access.ACCEPTED, access.DENIED, access.auth_message("X"), b"", b"\x45"):
        assert tunnel.packet_payload(control) is None
    assert access.parse_message(tunnel.packet_message(access.ACCEPTED)) is None


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
