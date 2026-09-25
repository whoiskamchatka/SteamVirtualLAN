"""IP packets between members, kept apart from control messages.

Every message on a connection between two members is exactly one of:

- a control message (access.py), which starts with access.PREFIX, b"SVL1 ",
  and is sent reliably;
- an IP packet: PACKET_PREFIX followed by the complete packet, sent
  unreliably, the way IP expects to be carried.

The first byte alone decides which it is. A control message is never written
to the adapter, and a packet is never parsed as a control message.
"""

from ipaddress import IPv4Address

from steamlan.adapter.ipv4 import is_member_address, parse_ipv4_header

PACKET_PREFIX = b"\x00"


def packet_message(packet: bytes) -> bytes:
    return PACKET_PREFIX + bytes(packet)


def packet_payload(data: bytes) -> bytes | None:
    """The IP packet in a packet message; None for any other message."""
    if data[:1] == PACKET_PREFIX:
        return data[1:]
    return None


def _addresses(packet: bytes) -> tuple[IPv4Address, IPv4Address] | None:
    """(source, destination) of an IPv4 packet; None for anything else."""
    try:
        header = parse_ipv4_header(packet)
    except ValueError:
        return None
    return IPv4Address(header.source), IPv4Address(header.destination)


def destination_member(
    packet: bytes, local_address: IPv4Address | None, owners: dict[IPv4Address, int]
) -> int | None:
    """The SteamID to send a packet from Windows to, or None to drop it.

    Only unicast IPv4 from this member's own address to another member's
    address is carried. Broadcast, multicast, IPv6 and anything addressed
    outside the network are dropped.
    """
    addresses = _addresses(packet)
    if addresses is None or local_address is None:
        return None
    source, destination = addresses
    if source != local_address or destination == local_address:
        return None
    return owners.get(destination)


def accepts_packet(
    packet: bytes, sender_address: IPv4Address | None, local_address: IPv4Address | None
) -> bool:
    """Whether a packet from a member may be written to the local adapter.

    It must come from the address the host gave that very member, and be
    addressed to this member's own address.
    """
    addresses = _addresses(packet)
    if addresses is None or sender_address is None or local_address is None:
        return False
    source, destination = addresses
    return (
        source == sender_address
        and destination == local_address
        and is_member_address(source)
        and source != local_address
    )
