"""IP packets between members, kept apart from control messages.

Every message on a connection between two members is exactly one of:

- a control message (access.py), which starts with access.PREFIX, b"SVL2 ",
  and is sent reliably, except PING and PONG (latency.py), which are sent
  unreliably like the packets whose round trip they measure;
- an IP packet: PACKET_PREFIX followed by the complete packet, sent
  unreliably, the way IP expects to be carried.

The first byte alone decides which it is. A control message is never written
to the adapter, and a packet is never parsed as a control message.

A packet from Windows is delivered by its destination (ipv4.Delivery): to
the one member that owns a unicast address, or, for the network's broadcast
address, 255.255.255.255 and multicast groups, one copy to every member this
member is connected to. Packets travel exactly as Windows wrote them; the
connection they arrive on says which member sent them. A packet that arrived
from a member is only ever handed to this member's Windows, never sent on to
anyone, so a broadcast can't go round in circles.
"""

from collections import Counter
from ipaddress import IPv4Address

from steamlan.adapter.ipv4 import IGMP, Addressing, Delivery, addressing, may_deliver

PACKET_PREFIX = b"\x00"


def packet_message(packet: bytes) -> bytes:
    return PACKET_PREFIX + bytes(packet)


def packet_payload(data: bytes) -> bytes | None:
    """The IP packet in a packet message; None for any other message."""
    if data[:1] == PACKET_PREFIX:
        return data[1:]
    return None


def outgoing(packet: bytes, local_address: IPv4Address | None) -> Addressing | None:
    """How to deliver a packet Windows sent into the adapter; None to drop it.

    Only IPv4 from this member's own address is carried, to another member's
    address, the network's broadcast address, 255.255.255.255 or a multicast
    group. IPv6, IGMP, packets to this member itself and anything addressed
    outside the network are dropped. Requiring this member's own source also
    means a packet that came from another member can never be sent on, even
    if Windows handed it back.
    """
    info = addressing(packet)
    if info is None or info.delivery is None or local_address is None:
        return None
    if info.source != local_address or info.destination == local_address:
        return None
    if info.protocol == IGMP:
        return None
    return info


def accepts_packet(
    packet: bytes, sender_address: IPv4Address | None, local_address: IPv4Address | None
) -> Delivery | None:
    """How a packet from a member may be written to the local adapter; None
    if it may not.

    It must come from the address that very member was given, and be
    addressed to this member's own address, the network's broadcast address,
    255.255.255.255 or a multicast group.
    """
    if sender_address is None:
        return None
    return may_deliver(packet, local_address, sender_address)


class TrafficStats:
    """Counts of the packets carried and dropped, for diagnosing games; never
    their contents.

    Sent counts packets from Windows, whatever the number of members they
    went to; copies counts the messages that actually went out.
    """

    def __init__(self):
        self._sent: Counter[Delivery] = Counter()
        self._received: Counter[Delivery] = Counter()
        self._dropped: Counter[str] = Counter()
        self.copies = 0

    def sent(self, delivery: Delivery, copies: int) -> None:
        self._sent[delivery] += 1
        self.copies += copies

    def received(self, delivery: Delivery) -> None:
        self._received[delivery] += 1

    def dropped(self, reason: str) -> None:
        self._dropped[reason] += 1

    @property
    def dropped_total(self) -> int:
        return self._dropped.total()

    def snapshot(self) -> dict:
        """E.g. {"tx": {"unicast": 3, ...}, "tx_copies": 5, "rx": {...},
        "dropped": {"rx_rejected": 1}}."""
        return {
            "tx": {delivery.value: self._sent[delivery] for delivery in Delivery},
            "tx_copies": self.copies,
            "rx": {delivery.value: self._received[delivery] for delivery in Delivery},
            "dropped": dict(self._dropped),
        }

    def summary(self) -> str:
        snapshot = self.snapshot()
        tx = " ".join(f"{kind}={count}" for kind, count in snapshot["tx"].items())
        rx = " ".join(f"{kind}={count}" for kind, count in snapshot["rx"].items())
        dropped = " ".join(f"{reason}={count}" for reason, count in sorted(self._dropped.items()))
        return f"tx {tx} (copies={self.copies}); rx {rx}; dropped {dropped or 'none'}"
