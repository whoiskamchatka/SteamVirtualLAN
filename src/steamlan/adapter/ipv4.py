"""The virtual network's addresses, and just enough of the IPv4 header to deliver packets."""

import enum
import ipaddress
from dataclasses import dataclass

PROTOCOLS = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP"}

# Every SteamVirtualLAN network uses this subnet. The member that creates a
# network gets the first address, .1; see app/roster.py.
VIRTUAL_NETWORK = ipaddress.IPv4Network("10.77.0.0/24")


def is_member_address(address: ipaddress.IPv4Address) -> bool:
    """Whether address can belong to a member: in the subnet, not its network or broadcast."""
    return address in VIRTUAL_NETWORK and address not in (
        VIRTUAL_NETWORK.network_address,
        VIRTUAL_NETWORK.broadcast_address,
    )


@dataclass(frozen=True)
class IPv4Header:
    source: str
    destination: str
    protocol: int
    header_length: int
    total_length: int

    @property
    def protocol_name(self) -> str:
        return PROTOCOLS.get(self.protocol, f"protocol {self.protocol}")


class Delivery(enum.Enum):
    """How an IPv4 packet is delivered on the virtual network, by its destination."""

    UNICAST = "unicast"  # one member's address in VIRTUAL_NETWORK
    BROADCAST = "broadcast"  # NETWORK_BROADCAST or LIMITED_BROADCAST: every member
    MULTICAST = "multicast"  # 224.0.0.0/4: every member (there is no IGMP tracking)


# The virtual network's own broadcast address, 10.77.0.255, and "everyone on
# this link", 255.255.255.255.
NETWORK_BROADCAST = VIRTUAL_NETWORK.broadcast_address
LIMITED_BROADCAST = ipaddress.IPv4Address("255.255.255.255")
MULTICAST_NETWORK = ipaddress.IPv4Network("224.0.0.0/4")
# IGMP is how a host tells multicast routers which groups it wants. Every
# multicast packet goes to every member anyway, so it is never carried.
IGMP = 2


@dataclass(frozen=True)
class Addressing:
    source: ipaddress.IPv4Address
    destination: ipaddress.IPv4Address
    protocol: int
    # None: the destination is nothing the virtual network delivers to
    # (outside it, its network address, ...).
    delivery: Delivery | None


def addressing(packet: bytes) -> Addressing | None:
    """Source, destination and delivery of an IPv4 packet; None for anything
    that isn't a well-formed IPv4 packet (IPv6 included)."""
    try:
        header = parse_ipv4_header(packet)
    except ValueError:
        return None
    destination = ipaddress.IPv4Address(header.destination)
    if destination in (NETWORK_BROADCAST, LIMITED_BROADCAST):
        delivery = Delivery.BROADCAST
    elif destination in MULTICAST_NETWORK:
        delivery = Delivery.MULTICAST
    elif is_member_address(destination):
        delivery = Delivery.UNICAST
    else:
        delivery = None
    return Addressing(ipaddress.IPv4Address(header.source), destination, header.protocol, delivery)


def may_deliver(
    packet: bytes,
    local_address: ipaddress.IPv4Address | None,
    sender_address: ipaddress.IPv4Address | None = None,
) -> Delivery | None:
    """How a packet that came from another member may be handed to Windows
    on the member with local_address; None if it may not.

    It must come from another member's address (sender_address, when the
    member that sent it is known: exactly that address), and be addressed to
    this member alone, to the network's broadcast address or to a multicast
    group. The packet itself is never changed.
    """
    info = addressing(packet)
    if info is None or info.delivery is None or local_address is None:
        return None
    source = info.source
    if not is_member_address(source) or source == local_address:
        return None
    if sender_address is not None and source != sender_address:
        return None
    if info.protocol == IGMP:
        return None
    if info.delivery is Delivery.UNICAST and info.destination != local_address:
        return None
    return info.delivery


def ip_version(packet: bytes) -> int | None:
    return packet[0] >> 4 if packet else None


def parse_ipv4_header(packet: bytes) -> IPv4Header:
    """Parse the fixed IPv4 header fields; ValueError if they don't fit the packet."""
    if len(packet) < 20:
        raise ValueError(f"{len(packet)} bytes is too short for an IPv4 header")
    if packet[0] >> 4 != 4:
        raise ValueError("not an IPv4 packet")
    header_length = (packet[0] & 0x0F) * 4
    if header_length < 20 or header_length > len(packet):
        raise ValueError(f"bad IPv4 header length {header_length}")
    total_length = int.from_bytes(packet[2:4], "big")
    if total_length < header_length or total_length > len(packet):
        raise ValueError(f"bad IPv4 total length {total_length} for {len(packet)} bytes")
    return IPv4Header(
        source=str(ipaddress.IPv4Address(packet[12:16])),
        destination=str(ipaddress.IPv4Address(packet[16:20])),
        protocol=packet[9],
        header_length=header_length,
        total_length=total_length,
    )


def describe_packet(packet: bytes) -> str:
    version = ip_version(packet)
    if version == 6:
        return f"IPv6 packet, {len(packet)} bytes"
    if version != 4:
        return f"Unknown packet, {len(packet)} bytes"
    try:
        header = parse_ipv4_header(packet)
    except ValueError as exc:
        return f"Malformed IPv4 packet, {len(packet)} bytes: {exc}"
    return (
        f"IPv4 {header.protocol_name} {header.source} -> {header.destination}, "
        f"{header.total_length} bytes"
    )
