import pytest
from app_fakes import GUEST, HOST, LISTEN_SOCKET, LOBBY, OTHER, FakeSteam, status

from steamlan.app import access, roster
from steamlan.app.latency import (
    MAX_PENDING,
    PING_INTERVAL,
    PING_TIMEOUT,
    SMOOTHING,
    STALE_AFTER,
    LatencyTracker,
    format_rtt,
)
from steamlan.app.network import NetworkSession
from steamlan.steam import ConnectionState

PEER, OTHER_PEER = 1, 2


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def tracker(clock):
    tracker = LatencyTracker(clock)
    tracker.track({PEER: 10})
    return tracker


def test_first_ping_goes_out_at_once_then_every_interval(tracker, clock):
    ((peer, first),) = tracker.due()
    assert peer == PEER
    assert tracker.due() == []

    clock.now += PING_INTERVAL - 0.01
    assert tracker.due() == []
    clock.now += 0.01
    ((_, second),) = tracker.due()
    assert second != first


def test_round_trip_time_from_the_local_clock(tracker, clock):
    ((_, sequence),) = tracker.due()
    clock.now += 0.024

    assert tracker.pong(PEER, sequence)

    assert tracker.rtt(PEER) == pytest.approx(0.024)
    assert format_rtt(tracker.rtt(PEER)) == "24 ms"


def test_no_rtt_before_the_first_answer(tracker):
    tracker.due()

    assert tracker.rtt(PEER) is None
    assert format_rtt(tracker.rtt(PEER)) == "— ms"


def test_rtt_is_smoothed(tracker, clock):
    for sample in (0.020, 0.100):
        ((_, sequence),) = tracker.due()
        clock.now += sample
        tracker.pong(PEER, sequence)
        clock.now += PING_INTERVAL

    assert tracker.rtt(PEER) == pytest.approx(0.020 + SMOOTHING * (0.100 - 0.020))


def test_duplicate_and_unknown_answers_are_ignored(tracker, clock):
    ((_, sequence),) = tracker.due()
    clock.now += 0.030
    assert tracker.pong(PEER, sequence)

    clock.now += 0.500
    assert not tracker.pong(PEER, sequence)
    assert not tracker.pong(PEER, sequence + 1000)
    assert not tracker.pong(OTHER_PEER, sequence)

    assert tracker.rtt(PEER) == pytest.approx(0.030)


def test_late_answer_to_a_lost_ping_is_ignored(tracker, clock):
    ((_, sequence),) = tracker.due()

    clock.now += PING_TIMEOUT + 0.1
    tracker.due()

    assert tracker.pending(PEER) == 1
    assert not tracker.pong(PEER, sequence)
    assert tracker.rtt(PEER) is None


def test_lost_pings_expire(tracker, clock):
    for _ in range(20):
        tracker.due()  # never answered
        clock.now += PING_INTERVAL

    # Only the PINGs of the last PING_TIMEOUT seconds are still waiting:
    # 0, 2, 4 and 6 seconds old.
    assert tracker.pending(PEER) == int(PING_TIMEOUT / PING_INTERVAL) + 1


def test_waiting_pings_are_capped(tracker, clock, monkeypatch):
    monkeypatch.setattr("steamlan.app.latency.PING_TIMEOUT", 1e9)
    sent = []
    for _ in range(10):
        sent += [sequence for _, sequence in tracker.due()]
        clock.now += PING_INTERVAL

    assert tracker.pending(PEER) == MAX_PENDING
    # The oldest were dropped: the newest still count.
    assert not tracker.pong(PEER, sent[0])
    assert tracker.pong(PEER, sent[-1])


def test_an_old_answer_still_counts_if_its_ping_is_still_waiting(tracker, clock):
    ((_, first),) = tracker.due()
    clock.now += PING_INTERVAL
    tracker.due()
    clock.now += 0.050

    assert tracker.pong(PEER, first)
    assert tracker.rtt(PEER) == pytest.approx(PING_INTERVAL + 0.050)


def test_rtt_goes_stale_without_answers(tracker, clock):
    ((_, sequence),) = tracker.due()
    clock.now += 0.010
    tracker.pong(PEER, sequence)

    clock.now += STALE_AFTER + 0.1

    assert tracker.rtt(PEER) is None


def test_new_connection_starts_over(tracker, clock):
    ((_, sequence),) = tracker.due()
    clock.now += 0.010
    tracker.pong(PEER, sequence)
    clock.now += PING_INTERVAL
    ((_, waiting),) = tracker.due()

    tracker.track({PEER: 11})

    assert tracker.rtt(PEER) is None
    assert tracker.pending(PEER) == 0
    # An answer that was meant for the old connection doesn't count.
    assert not tracker.pong(PEER, waiting)
    ((_, new),) = tracker.due()
    assert new > sequence


def test_members_that_are_not_connected_are_forgotten(tracker, clock):
    ((_, sequence),) = tracker.due()

    tracker.track({})

    assert tracker.due() == []
    assert not tracker.pong(PEER, sequence)
    assert tracker.rtt(PEER) is None


def test_every_member_has_its_own_rtt(clock):
    tracker = LatencyTracker(clock)
    tracker.track({PEER: 10, OTHER_PEER: 20})
    pings = dict(tracker.due())

    clock.now += 0.015
    tracker.pong(PEER, pings[PEER])
    clock.now += 0.065
    tracker.pong(OTHER_PEER, pings[OTHER_PEER])
    # A member can't answer for another.
    assert not tracker.pong(PEER, pings[OTHER_PEER])

    assert tracker.rtt(PEER) == pytest.approx(0.015)
    assert tracker.rtt(OTHER_PEER) == pytest.approx(0.080)


def test_sequence_numbers_never_repeat(clock):
    tracker = LatencyTracker(clock)
    tracker.track({PEER: 10, OTHER_PEER: 20})
    seen = []
    for _ in range(5):
        seen += [sequence for _, sequence in tracker.due()]
        clock.now += PING_INTERVAL

    assert len(seen) == len(set(seen)) == 10


@pytest.mark.parametrize(
    ("rtt", "text"), [(None, "— ms"), (0.0003, "<1 ms"), (0.0244, "24 ms"), (0.2, "200 ms")]
)
def test_format_rtt(rtt, text):
    assert format_rtt(rtt) == text


# In the network session


A1, A2, A3 = "10.77.0.1", "10.77.0.2", "10.77.0.3"


def coordinator_with(clock, *members):
    steam = FakeSteam(HOST, [HOST, *members])
    network = NetworkSession(
        steam, LOBBY, LISTEN_SOCKET, "7K2QDM9XTE", clock, known={HOST: roster.IPv4Address(A1)}
    )
    connections = {}
    for steam_id in members:
        network.process([])
        connection = [c[1] for c in steam.called("connect_p2p") if c[0] == steam_id][-1]
        network.process([status(connection, ConnectionState.CONNECTED, steam_id)])
        steam.inbox[connection] = [access.auth_message("7K2QDM9XTE")]
        network.process([])
        connections[steam_id] = connection
    return steam, network, connections


def pings(steam):
    return [
        (connection, message.sequence)
        for connection, data in steam.unreliable
        if (message := access.parse_message(data)) is not None and message.kind == "ping"
    ]


def views(network):
    return {view.steam_id: view for view in network.members()}


def test_session_pings_connected_members_every_two_seconds(clock):
    steam, network, connections = coordinator_with(clock, GUEST, OTHER)
    first = pings(steam)
    assert sorted(connection for connection, _ in first) == sorted(connections.values())

    network.process([])
    assert len(pings(steam)) == 2
    clock.now += PING_INTERVAL
    network.process([])
    assert len(pings(steam)) == 4


def test_session_shows_the_rtt_of_each_member(clock):
    steam, network, connections = coordinator_with(clock, GUEST, OTHER)
    sequences = {connection: sequence for connection, sequence in pings(steam)}
    assert views(network)[GUEST].latency == "— ms"

    clock.now += 0.012
    steam.inbox[connections[GUEST]] = [access.pong_message(sequences[connections[GUEST]])]
    network.process([])
    clock.now += 0.030
    steam.inbox[connections[OTHER]] = [access.pong_message(sequences[connections[OTHER]])]
    network.process([])

    assert views(network)[GUEST].latency == "12 ms"
    assert views(network)[OTHER].latency == "42 ms"
    assert views(network)[HOST].latency == ""


def test_session_answers_pings(clock):
    steam, network, connections = coordinator_with(clock, GUEST)
    steam.unreliable.clear()

    steam.inbox[connections[GUEST]] = [access.ping_message(77)]
    network.process([])

    assert (connections[GUEST], access.pong_message(77)) in steam.unreliable
    assert steam.sent[-1][1] != access.pong_message(77)


def test_reconnected_member_starts_without_latency(clock):
    steam, network, connections = coordinator_with(clock, GUEST)
    ((connection, sequence),) = pings(steam)
    clock.now += 0.020
    steam.inbox[connection] = [access.pong_message(sequence)]
    network.process([])
    assert views(network)[GUEST].latency == "20 ms"

    network.process([status(connection, ConnectionState.PROBLEM_DETECTED_LOCALLY, GUEST)])
    assert views(network)[GUEST].latency == ""
    assert views(network)[GUEST].status == "Reconnecting..."

    clock.now += 10
    network.process([])
    new = [c[1] for c in steam.called("connect_p2p") if c[0] == GUEST][-1]
    network.process([status(new, ConnectionState.CONNECTED, GUEST)])

    assert new != connection
    assert views(network)[GUEST].latency == "— ms"
    steam.inbox[new] = [access.pong_message(sequence)]
    network.process([])
    assert views(network)[GUEST].latency == "— ms"


def test_suspended_session_does_not_ping(clock):
    steam, network, connections = coordinator_with(clock, GUEST)
    steam.unreliable.clear()

    network.suspend()
    clock.now += 10
    network.process([])

    assert pings(steam) == []
