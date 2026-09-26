"""Losing and getting back the connection, on the simulated Steam (network_harness.py).

This PC losing its connection is not the other members going offline: the
network stays on screen as last seen, behind an overlay, and comes back by
itself. The other members tell a member that dropped out apart from one that
went offline.
"""

import pytest
from network_harness import A1, A2, A3, A, B, C, Net, three_members

from steamlan.adapter.launcher import State
from steamlan.app.controller import (
    NETWORK_GONE,
    RESTORE_FAILED,
    RESTORE_TIMEOUT,
    Link,
    Presence,
    Screen,
)


@pytest.fixture
def net(tmp_path):
    return Net(tmp_path)


def rows(app):
    return {m.steam_id: (m.name, m.address, m.status) for m in app.view().members}


def joins(steam):
    return [call for call in steam.calls if call[0] == "request_join_lobby"]


def open_connections(steam):
    """remote SteamID -> number of open connections to it."""
    counts = {}
    for connection in steam.connections.values():
        if connection.open:
            counts[connection.remote] = counts.get(connection.remote, 0) + 1
    return counts


def lose(net, steam_id, **how):
    net.steam(steam_id).lose_connection(**how)
    net.run()


def test_losing_the_connection_keeps_the_network_on_screen(net):
    three_members(net)
    bob = net.apps[B]
    before = rows(bob)
    helper = bob.helper

    lose(net, B)

    view = bob.view()
    assert (view.screen, view.presence) == (Screen.NETWORK, Presence.RESTORING)
    assert view.overlay == "Restoring connection..."
    assert view.overlay_detail == "SteamVirtualLAN is reconnecting"
    assert view.tray_status == "Restoring connection..."
    assert view.network_status == "Restoring connection..."
    # As last seen: nobody is suddenly Offline, and nothing is measured.
    assert rows(bob) == before
    assert {status for _, _, status in rows(bob).values()} == {"Online"}
    assert all(member.latency == "" for member in view.members)
    assert not view.can_invite
    # The adapter and the membership stay.
    assert bob.helper is helper and helper.state is State.READY
    assert bob.saved.online
    assert bob.store.load(B).address_of(B) == A2


def test_everything_comes_back_by_itself(net):
    three_members(net)
    bob = net.apps[B]
    helper = bob.helper
    lose(net, B)

    net.steam(B).restore_connection()
    net.run(20)

    view = bob.view()
    assert view.presence is Presence.ONLINE
    assert view.overlay == ""
    assert bob.link is None
    assert bob.network.local_address == A2
    assert {status for _, _, status in rows(bob).values()} == {"Online"}
    assert net.ping(B, C) and net.ping(C, B) and net.ping(A, B)
    # The same adapter and helper throughout, and one connection per member.
    assert bob.helper is helper and bob.helpers == [helper]
    assert open_connections(net.steam(B)) == {A: 1, C: 1}
    assert net.roster() == {A: A1, B: A2, C: A3}


def test_short_outage_resumes_without_joining_again(net):
    three_members(net)
    bob = net.apps[B]
    lose(net, B, drop_from_lobby=False, drop_connections=False)
    assert bob.presence() is Presence.RESTORING
    tries = len(joins(net.steam(B)))

    net.steam(B).restore_connection()
    net.run(2)

    assert bob.presence() is Presence.ONLINE
    assert len(joins(net.steam(B))) == tries
    assert open_connections(net.steam(B)) == {A: 1, C: 1}
    assert net.ping(B, C)


def test_connections_that_dropped_during_a_short_outage_come_back(net):
    three_members(net)
    bob = net.apps[B]
    lose(net, B, drop_from_lobby=False)
    assert open_connections(net.steam(B)) == {}

    net.steam(B).restore_connection()
    # Alice connects to Bob, and does so again 5 seconds after hers dropped.
    net.run(140)

    assert bob.presence() is Presence.ONLINE
    assert open_connections(net.steam(B)) == {A: 1, C: 1}
    assert net.ping(B, A) and net.ping(B, C)


def test_other_members_see_a_member_that_dropped_out_as_lost_not_offline(net):
    alice = three_members(net)

    lose(net, B)
    assert rows(alice)[B][2] == "Lost connection"

    net.steam(B).restore_connection()
    net.run(20)
    assert rows(alice)[B][2] == "Online"

    net.apps[C].go_offline()
    net.run()
    assert rows(alice)[C][2] == "Offline"


def test_remote_member_that_is_briefly_unreachable_is_reconnecting(net):
    alice = three_members(net)

    # Only the connections drop; everybody stays in the lobby and on Steam.
    net.steam(B)._drop_connections(notify_self=True)
    net.run(2)

    assert alice.presence() is Presence.ONLINE
    assert rows(alice)[B][2] == "Reconnecting..."
    assert net.apps[B].presence() is Presence.ONLINE

    net.run(140)
    assert rows(alice)[B][2] == "Online"


def test_coordinator_losing_its_connection_comes_back_as_a_member(net):
    three_members(net)
    assert net.owner() == A

    lose(net, A)
    assert net.owner() in (B, C)
    assert net.ping(B, C)

    net.steam(A).restore_connection()
    net.run(20)

    alice = net.apps[A]
    assert alice.presence() is Presence.ONLINE
    assert alice.network.local_address == A1
    assert not alice.network.is_coordinator
    assert net.ping(A, C)


def test_steam_lost_again_while_restoring(net):
    three_members(net)
    bob = net.apps[B]
    lose(net, B)
    net.steam(B).fail_joins = True
    net.steam(B).restore_connection()
    net.run(4)
    assert bob.link is Link.RESTORING_NETWORK
    assert bob.view().overlay_detail == "Rejoining your network..."

    lose(net, B)
    assert bob.link is Link.RECONNECTING
    assert bob.view().overlay_detail == "SteamVirtualLAN is reconnecting"

    net.steam(B).fail_joins = False
    net.steam(B).restore_connection()
    net.run(20)
    assert bob.presence() is Presence.ONLINE
    assert bob.helpers == [bob.helper]


def test_joining_again_backs_off(net):
    three_members(net)
    lose(net, B)
    steam = net.steam(B)
    steam.fail_joins = True
    tries = len(joins(steam))
    steam.restore_connection()

    times = []
    for _ in range(500):
        before = len(joins(steam))
        net.run(1, step=0.1)
        if len(joins(steam)) > before:
            times.append(round(net.clock.now - 0.1, 1))

    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    # 2, 4, 8, then every 15 seconds, give or take one 0.1 s tick.
    assert gaps[:5] == pytest.approx([2.0, 4.0, 8.0, 15.0, 15.0], abs=0.25)
    assert len(joins(steam)) - tries == len(times)


def test_restoring_gives_up_after_a_while_but_keeps_the_membership(net):
    three_members(net)
    bob = net.apps[B]
    lose(net, B)
    net.steam(B).fail_joins = True
    net.steam(B).restore_connection()

    net.run(int(RESTORE_TIMEOUT) + 20, step=1.0)

    view = bob.view()
    assert view.presence is Presence.OFFLINE
    assert view.error == RESTORE_FAILED
    assert bob.helper is None
    # Not the user's choice: the next start tries again.
    assert bob.store.load(B).online
    assert len(joins(net.steam(B))) < 12


def test_network_gone_while_restoring(net):
    three_members(net)
    bob = net.apps[B]
    helper = bob.helper
    lose(net, B)
    net.stop(A)
    net.stop(C)
    assert net.world.lobbies == {}

    net.steam(B).restore_connection()
    net.run()

    view = bob.view()
    assert (view.screen, view.presence) == (Screen.HOME, Presence.NONE)
    assert view.error == NETWORK_GONE
    assert view.overlay == ""
    assert helper.state is State.STOPPED and bob.helper is None
    assert bob.store.load(B) is None


def test_go_offline_while_restoring_stops_reconnecting(net):
    three_members(net)
    bob = net.apps[B]
    helper = bob.helper
    lose(net, B)
    tries = len(joins(net.steam(B)))

    bob.go_offline()
    net.steam(B).restore_connection()
    net.run(40, step=1.0)

    assert bob.presence() is Presence.OFFLINE
    assert bob.view().overlay == ""
    assert len(joins(net.steam(B))) == tries
    assert helper.state is State.STOPPED
    assert not bob.store.load(B).online


def test_go_offline_is_not_undone_when_steam_comes_back(net):
    three_members(net)
    bob = net.apps[B]
    bob.go_offline()
    net.run()
    tries = len(joins(net.steam(B)))

    lose(net, B)
    net.steam(B).restore_connection()
    net.run(40, step=1.0)

    assert bob.presence() is Presence.OFFLINE
    assert len(joins(net.steam(B))) == tries


def test_leave_network_while_restoring(net):
    three_members(net)
    bob = net.apps[B]
    helper = bob.helper
    lose(net, B)

    bob.leave_network()
    net.steam(B).restore_connection()
    net.run(20)

    assert bob.presence() is Presence.NONE
    assert bob.view().screen is Screen.HOME
    assert helper.state is State.STOPPED
    assert bob.store.load(B) is None
    assert B not in net.world.lobbies[next(iter(net.world.lobbies))].members


def test_leave_network_after_recovering_still_frees_the_address(net):
    three_members(net)
    lose(net, B)
    net.steam(B).restore_connection()
    net.run(20)

    net.apps[B].leave_network()
    net.run()

    assert net.roster() == {A: A1, C: A3}


def test_exit_while_restoring_cleans_up_and_comes_back_later(net):
    three_members(net)
    bob = net.apps[B]
    steam, helper = bob.steam, bob.helper
    lose(net, B)

    net.stop(B)

    assert helper.state is State.STOPPED
    assert not steam.running
    assert steam.connections == {} and steam.listen_sockets == set()
    assert bob.store.load(B).online

    bob = net.start(B)
    net.run(20)
    assert bob.presence() is Presence.ONLINE
    assert bob.network.local_address == A2


def test_starting_while_steam_is_unreachable(net):
    three_members(net)
    net.stop(B)

    bob = net.start(B, logged_on=False)
    net.run()

    view = bob.view()
    assert (view.screen, view.presence) == (Screen.NETWORK, Presence.RESTORING)
    assert view.overlay == "Restoring connection..."
    assert {(m.address, m.status) for m in view.members} == {
        ("10.77.0.1", "—"),
        ("10.77.0.2", "—"),
        ("10.77.0.3", "—"),
    }
    assert bob.helpers == []

    net.steam(B).restore_connection()
    net.run(20)
    assert bob.presence() is Presence.ONLINE
    assert len(bob.helpers) == 1


def test_members_show_their_latency(net):
    alice = three_members(net)
    net.run(4)

    members = {m.steam_id: m for m in alice.view().members}
    assert members[A].latency == ""
    for steam_id in (B, C):
        assert members[steam_id].latency.endswith(" ms")
        assert members[steam_id].latency != "— ms"
