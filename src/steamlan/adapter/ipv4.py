"""Just enough of the IPv4 header to describe packets in diagnostics."""

import ipaddress
from dataclasses import dataclass

PROTOCOLS = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP"}


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
