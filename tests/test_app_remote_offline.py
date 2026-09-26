"""Other members going offline or away is not this PC losing its connection.

The two-PC case these reproduce: PC A created the network and owns the Steam
lobby; PC B joined and then chose Go Offline. A must simply show B as Offline
and stay online, in the same lobby, with the same adapter; before, A decided
its own connection was gone, tried to get back into its own lobby and in the
end lost the network.

Each test runs twice: with a Steam whose local copy of the lobby is always
consistent, and with one whose member list briefly can't be read after a
member leaves (steam_sim.World.unsettled_reads), as a real Steam client's can.
"""

from ipaddress import IPv4Address

import pytest
from network_harness import Net

from steamlan.adapter.launcher import State
from steamlan.app import roster
from steamlan.app.controller import Presence

# The SteamIDs from the real report.
PC_A = 76561198005420180
PC_B = 76561198666780767
PC_C = 76561198666780768
A1, A2, A3 = (IPv4Address(f"10.77.0.{n}") for n in (1, 2, 3))


@pytest.fixture(params=[0, 3], ids=["settled lobby", "unsettled lobby reads"])
def net(tmp_path, request):
    net = Net(tmp_path)
    net.world.unsettled_reads = request.param
    return net


def join(net, steam_id, creator):
    app = net.start(steam_id)
    view = creator.view()
    app.show_join()
    app.join(str(view.lobby_id), view.access_code)
    net.run()
    return app


def calls(app, name):
    return [call for call in app.steam.calls if call[0] == name]


def rows(app):
    return {m.steam_id: (m.status, m.address) for m in app.view().members}


def lobby(net):
    (lobby,) = net.world.lobbies.values()
    return lobby


class Watch:
    """What an app looks like before something happens to other members."""

    def __init__(self, app):
        self.app = app
        self.helper = app.helper
        self.joins = len(calls(app, "request_join_lobby"))
        self.lobby_id = app.network.lobby_id
        # Every time the app decided it had lost its own connection, even if
        # only for a moment.
        self.losses = []
        lose_connection = app._lose_connection

        def recorded(*args, **kwargs):
            self.losses.append(args)
            return lose_connection(*args, **kwargs)

        app._lose_connection = recorded

    def assert_undisturbed(self):
        """Still online in the same lobby, with the same adapter, and never
        tried to recover: no leaving, no joining again."""
        app, helper = self.app, self.helper
        assert self.losses == []
        assert app.presence() is Presence.ONLINE
        assert app.link is None
        assert app.view().overlay == ""
        assert not app.network.suspended
        assert app.network.lobby_id == self.lobby_id
        assert app.helper is helper and helper.state is State.READY
        assert app.helpers == [helper]
        assert calls(app, "leave_lobby") == []
        assert len(calls(app, "request_join_lobby")) == self.joins


def test_owner_stays_online_when_a_member_goes_offline(net):
    """The real repro."""
    a = net.create(PC_A)
    b = join(net, PC_B, a)
    assert b.network.local_address == A2
    assert net.owner() == PC_A
    watch = Watch(a)

    b.go_offline()
    net.run(200, step=0.25)

    watch.assert_undisturbed()
    assert rows(a)[PC_B] == ("Offline", "10.77.0.2")
    assert lobby(net).members == [PC_A] and lobby(net).owner == PC_A
    assert net.roster() == {PC_A: A1, PC_B: A2}
    assert a.store.load(PC_A).address_of(PC_B) == A2
    # B chose this: it stays a member, offline, with its address.
    assert b.presence() is Presence.OFFLINE
    assert b.store.load(PC_B).address_of(PC_B) == A2
    assert not b.store.load(PC_B).online


def test_network_of_one_stays_online(net):
    a = net.create(PC_A)
    watch = Watch(a)

    net.run(400, step=0.5)

    watch.assert_undisturbed()
    assert [m.status for m in a.view().members] == ["Online"]


def test_member_back_online_gets_its_address_again(net):
    a = net.create(PC_A)
    b = join(net, PC_B, a)
    watch = Watch(a)
    b.go_offline()
    net.run(40, step=0.25)

    b.go_online()
    net.run(20)

    assert b.presence() is Presence.ONLINE
    assert b.network.local_address == A2
    assert rows(a)[PC_B] == ("Online", "10.77.0.2")
    watch.assert_undisturbed()
    assert net.ping(PC_A, PC_B) and net.ping(PC_B, PC_A)


def test_owner_stays_online_when_a_member_loses_its_connection(net):
    a = net.create(PC_A)
    join(net, PC_B, a)
    watch = Watch(a)

    net.steam(PC_B).lose_connection()
    net.run(200, step=0.25)

    watch.assert_undisturbed()
    # Not something B chose: shown differently from Offline, address kept.
    assert rows(a)[PC_B] == ("Lost connection", "10.77.0.2")
    assert net.roster() == {PC_A: A1, PC_B: A2}


def test_member_stays_online_when_the_owner_goes_offline(net):
    a = net.create(PC_A)
    b = join(net, PC_B, a)
    watch = Watch(b)

    a.go_offline()
    net.run(200, step=0.25)

    # Steam made B the owner; B carries on as the coordinator.
    assert lobby(net).owner == PC_B
    assert b.network.is_coordinator
    watch.assert_undisturbed()
    assert rows(b)[PC_A] == ("Offline", "10.77.0.1")


def test_owner_stays_online_when_the_last_other_members_go_offline(net):
    a = net.create(PC_A)
    b = join(net, PC_B, a)
    c = join(net, PC_C, a)
    watch = Watch(a)

    b.go_offline()
    net.run(20, step=0.25)
    c.go_offline()
    net.run(200, step=0.25)

    watch.assert_undisturbed()
    assert rows(a) == {
        PC_A: ("Online", "10.77.0.1"),
        PC_B: ("Offline", "10.77.0.2"),
        PC_C: ("Offline", "10.77.0.3"),
    }
    assert lobby(net).members == [PC_A]


def test_member_leaving_the_network_frees_its_address(net):
    a = net.create(PC_A)
    b = join(net, PC_B, a)
    watch = Watch(a)

    b.leave_network()
    net.run(200, step=0.25)

    watch.assert_undisturbed()
    assert net.roster() == {PC_A: A1}
    assert PC_B not in rows(a)
    assert roster.decode(lobby(net).data[roster.ROSTER_KEY]) == {PC_A: A1}


def test_losing_the_connection_here_still_restores_it(net):
    a = net.create(PC_A)
    b = join(net, PC_B, a)
    helper = b.helper

    net.steam(PC_B).lose_connection()
    net.run(4)
    assert b.presence() is Presence.RESTORING
    assert b.view().overlay == "Restoring connection..."

    net.steam(PC_B).restore_connection()
    net.run(20)
    assert b.presence() is Presence.ONLINE
    assert b.helper is helper and b.helpers == [helper]
    assert b.network.local_address == A2
