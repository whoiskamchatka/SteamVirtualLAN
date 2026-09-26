"""Round-trip time to each connected member, from our own PING/PONG messages.

Every PING_INTERVAL seconds a member sends each connected member a PING with
a new sequence number, over the same Steam connection the packets use, and
the other side answers with a PONG carrying the same number. Only this side's
monotonic clock is used: the time a PING was sent is kept here, keyed by its
number, and never leaves this PC.

A PONG counts only if it answers a PING that is still waiting: unknown,
duplicate and late answers (the PING is older than PING_TIMEOUT) are
ignored. A lost PING just expires; at most MAX_PENDING wait at once.
Sequence numbers never repeat within a run, so an answer meant for a
connection that has since been replaced can't be mistaken for a new one; the
state starts over for every new connection.

The RTT shown is smoothed (an exponentially weighted average, like TCP's
SRTT) so it doesn't jump with every sample. None of this affects who is a
member of the network.
"""

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field

PING_INTERVAL = 2.0
# A PING unanswered for this long is taken as lost.
PING_TIMEOUT = 6.0
MAX_PENDING = 4
# Weight of a new sample in the smoothed RTT.
SMOOTHING = 0.25
# Without a new sample for this long, the RTT is no longer shown.
STALE_AFTER = 8.0


@dataclass
class _Peer:
    connection: int
    next_ping_at: float
    # Sequence number -> monotonic time the PING was sent.
    pending: dict[int, float] = field(default_factory=dict)
    rtt: float | None = None
    sampled_at: float = 0.0


class LatencyTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._peers: dict[int, _Peer] = {}
        self._sequence = itertools.count(1)

    def track(self, connections: dict[int, int]) -> None:
        """Follow the connected members: steam_id -> connection handle.

        A member with a new connection starts over; members that aren't
        connected are forgotten.
        """
        for steam_id in self._peers.keys() - connections.keys():
            del self._peers[steam_id]
        for steam_id, connection in connections.items():
            peer = self._peers.get(steam_id)
            if peer is None or peer.connection != connection:
                # The first PING goes out at once.
                self._peers[steam_id] = _Peer(connection, self.clock())

    def due(self) -> list[tuple[int, int]]:
        """(steam_id, sequence) of the PINGs to send now."""
        now = self.clock()
        pings = []
        for steam_id, peer in self._peers.items():
            self._expire(peer, now)
            if now < peer.next_ping_at:
                continue
            sequence = next(self._sequence)
            peer.pending[sequence] = now
            while len(peer.pending) > MAX_PENDING:
                del peer.pending[min(peer.pending)]
            peer.next_ping_at = now + PING_INTERVAL
            pings.append((steam_id, sequence))
        return pings

    def pong(self, steam_id: int, sequence: int) -> bool:
        """Take an answer; False if it was ignored."""
        peer = self._peers.get(steam_id)
        if peer is None:
            return False
        now = self.clock()
        self._expire(peer, now)
        sent = peer.pending.pop(sequence, None)
        if sent is None:
            return False
        sample = now - sent
        peer.rtt = sample if peer.rtt is None else peer.rtt + SMOOTHING * (sample - peer.rtt)
        peer.sampled_at = now
        return True

    def rtt(self, steam_id: int) -> float | None:
        """The smoothed RTT in seconds; None while there is no recent one."""
        peer = self._peers.get(steam_id)
        if peer is None or peer.rtt is None or self.clock() - peer.sampled_at > STALE_AFTER:
            return None
        return peer.rtt

    def pending(self, steam_id: int) -> int:
        peer = self._peers.get(steam_id)
        return len(peer.pending) if peer is not None else 0

    def clear(self) -> None:
        self._peers.clear()

    @staticmethod
    def _expire(peer: _Peer, now: float) -> None:
        for sequence in [s for s, sent in peer.pending.items() if now - sent > PING_TIMEOUT]:
            del peer.pending[sequence]


def format_rtt(rtt: float | None) -> str:
    """ "24 ms"; "— ms" while there is no recent measurement."""
    if rtt is None:
        return "— ms"
    milliseconds = round(rtt * 1000)
    return f"{milliseconds} ms" if milliseconds >= 1 else "<1 ms"
