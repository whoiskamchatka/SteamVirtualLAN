"""Whole networks: several apps on the simulated Steam in steam_sim.py.

Each app is an AppController with its own saved state and a FakeHelper in
place of the adapter, so these run without Steam, UAC or Wintun.
"""

from ipaddress import IPv4Address

import pytest
from app_fakes import GUEST, HOST, OTHER, FakeHelper, ipv4_packet
from steam_sim import World

from steamlan.adapter.launcher import State
from steamlan.app import access, roster, tunnel
from steamlan.app.controller import NETWORK_GONE, AppController, Presence, Screen
from steamlan.app.state import StateStore

A, B, C, D = HOST, GUEST, OTHER, OTHER + 1
A1, A2, A3, A4 = (IPv4Address(f"10.77.0.{n}") for n in (1, 2, 3, 4))
NAMES = {A: "Alice", B: "Bob", C: "Carol", D: "Dave"}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Net:
    """The simulated Steam and one PC per SteamID, each with its own saved state."""

    def __init__(self, tmp_path, pick_owner=None):
        self.world = World(pick_owner)
        self.clock = Clock()
        self.tmp_path = tmp_path
        self.apps: dict[int, AppController] = {}
        # Every message sent: (from, to, data).
        self.wire = []

    def start(self, steam_id) -> AppController:
        """Start SteamVirtualLAN on steam_id's PC (again)."""
        steam = self.world.client(steam_id, NAMES[steam_id])
        send = steam.send_message

        def recorded(handle, data, reliable=True):
            connection = steam.connections.get(handle)
            send(handle, data, reliable)
            self.wire.append((steam_id, connection.remote, bytes(data)))

        steam.send_message = recorded
        store = StateStore(self.tmp_path / str(steam_id) / "state.json")
        app = AppController(lambda: steam, self.clock, lambda: FakeHelper(steam), store)
        app.start()
        self.apps[steam_id] = app
        return app

    def stop(self, steam_id):
        """Exit, as from the tray menu."""
        self.apps.pop(steam_id).shutdown()

    def crash(self, steam_id):
        """The PC loses power: nothing is cleaned up or saved."""
        app = self.apps.pop(steam_id)
        app.steam.crash()

    def run(self, ticks=8):
        for _ in range(ticks):
            for app in list(self.apps.values()):
                app.tick()
                if app.helper is not None and app.helper.state is State.STARTING:
                    app.helper.become_ready()
            self.clock.now += 0.05

    def create(self, steam_id) -> AppController:
        app = self.start(steam_id)
        app.create_lobby()
        self.run()
        assert app.presence() is Presence.ONLINE
        return app

    def join(self, steam_id, creator: AppController) -> AppController:
        app = self.start(steam_id)
        view = creator.view()
        app.show_join()
        app.join(str(view.lobby_id), view.access_code)
        self.run()
        return app

    def owner(self):
        (lobby,) = self.world.lobbies.values()
        return lobby.owner

    def roster(self):
        (lobby,) = self.world.lobbies.values()
        return roster.decode(lobby.data[roster.ROSTER_KEY])

    def ping(self, source, destination) -> bool:
        """Windows on source sends a packet to destination's address."""
        sender, receiver = self.apps[source], self.apps[destination]
        packet = ipv4_packet(sender.network.local_address, receiver.network.local_address)
        sender.helper.from_windows.append(packet)
        self.run(2)
        return packet in receiver.helper.to_windows

    def packets_between(self):
        return {(a, b) for a, b, data in self.wire if tunnel.packet_payload(data) is not None}


def statuses(app):
    return {member.steam_id: (member.status, member.address) for member in app.view().members}


@pytest.fixture
def net(tmp_path):
    return Net(tmp_path)


def three_members(net):
    alice = net.create(A)
    net.join(B, alice)
    net.join(C, alice)
    net.run()
    return alice


def test_members_are_admitted_with_addresses_in_order(net):
    alice = three_members(net)

    assert net.roster() == {A: A1, B: A2, C: A3}
    for app in net.apps.values():
        assert app.presence() is Presence.ONLINE
        assert app.network.roster == {A: A1, B: A2, C: A3}
    assert statuses(alice) == {
        A: ("Online", "10.77.0.1"),
        B: ("Online", "10.77.0.2"),
        C: ("Online", "10.77.0.3"),
    }
    # Every member knows the code now, so every member can invite.
    assert all(app.view().can_invite for app in net.apps.values())


def test_packets_go_directly_between_the_two_members(net):
    three_members(net)

    assert net.ping(B, C)
    assert net.ping(C, B)
    assert net.ping(A, C)

    assert net.packets_between() == {(B, C), (C, B), (A, C)}


def test_members_keep_talking_when_the_creator_goes_offline(net):
    alice = three_members(net)

    alice.go_offline()
    net.run()

    assert alice.presence() is Presence.OFFLINE
    assert net.owner() in (B, C)
    assert net.ping(B, C)
    assert net.ping(C, B)
    bob = net.apps[B]
    assert statuses(bob)[A] == ("Offline", "10.77.0.1")
    assert net.roster() == {A: A1, B: A2, C: A3}


@pytest.mark.parametrize("pick", ["oldest", "newest"])
def test_whoever_steam_makes_owner_coordinates(tmp_path, pick):
    choose = {"oldest": lambda lobby: lobby.members[0], "newest": lambda lobby: lobby.members[-1]}
    net = Net(tmp_path, pick_owner=choose[pick])
    alice = three_members(net)

    alice.go_offline()
    net.run()

    expected = B if pick == "oldest" else C
    assert net.owner() == expected
    assert net.apps[expected].network.is_coordinator
    online = [app for app in net.apps.values() if app.network is not None]
    assert [app.network.is_coordinator for app in online].count(True) == 1


def test_new_member_is_admitted_while_the_creator_is_offline(net):
    alice = three_members(net)
    code = alice.view().access_code
    lobby_id = alice.view().lobby_id
    alice.go_offline()
    net.run()

    dave = net.start(D)
    dave.show_join()
    dave.join(str(lobby_id), code)
    net.run()

    assert dave.presence() is Presence.ONLINE
    # Alice's address stays hers while she is offline.
    assert net.roster() == {A: A1, B: A2, C: A3, D: A4}
    assert net.ping(D, B)


def test_returning_creator_is_an_ordinary_member_with_its_old_address(net):
    alice = three_members(net)
    alice.go_offline()
    net.run()
    coordinator = net.owner()

    alice.go_online()
    net.run()

    assert alice.presence() is Presence.ONLINE
    assert alice.network.local_address == A1
    assert net.owner() == coordinator
    assert not alice.network.is_coordinator
    assert net.ping(A, B)


def test_coordinator_that_disappears_is_replaced(net):
    three_members(net)
    net.apps[A].go_offline()
    net.run()
    coordinator = net.owner()
    survivor = C if coordinator == B else B

    net.crash(coordinator)
    net.run()

    assert net.owner() == survivor
    assert net.apps[survivor].network.is_coordinator
    assert statuses(net.apps[survivor])[coordinator][0] == "Offline"
    assert net.roster()[coordinator] == {B: A2, C: A3}[coordinator]


def test_member_that_crashed_comes_back_with_its_address(net):
    three_members(net)

    net.crash(C)
    net.run()
    carol = net.start(C)
    net.run()

    assert carol.presence() is Presence.ONLINE
    assert carol.network.local_address == A3
    assert net.ping(C, A)


def test_exit_is_not_leave(net):
    three_members(net)

    net.stop(B)
    net.run()
    assert net.roster() == {A: A1, B: A2, C: A3}
    assert statuses(net.apps[A])[B] == ("Offline", "10.77.0.2")

    bob = net.start(B)
    # Back online by itself, without asking for the code.
    assert bob.presence() is Presence.CONNECTING
    net.run()
    assert bob.presence() is Presence.ONLINE
    assert bob.network.local_address == A2


def test_going_offline_is_remembered_across_restarts(net):
    three_members(net)
    net.apps[B].go_offline()
    net.stop(B)

    bob = net.start(B)
    net.run()

    assert bob.presence() is Presence.OFFLINE
    assert bob.view().screen is Screen.NETWORK
    assert statuses(bob)[B] == ("Offline", "10.77.0.2")
    bob.go_online()
    net.run()
    assert bob.presence() is Presence.ONLINE
    assert bob.network.local_address == A2


def test_leave_network_frees_the_address(net):
    alice = three_members(net)

    net.apps[B].leave_network()
    net.run()

    assert net.roster() == {A: A1, C: A3}
    bob = net.apps[B]
    assert bob.presence() is Presence.NONE
    assert bob.saved is None and bob.store.load(B) is None
    assert B not in statuses(alice)

    dave = net.join(D, alice)
    assert dave.network.local_address == A2


def test_leave_network_while_offline_still_frees_the_address(net):
    three_members(net)
    bob = net.apps[B]
    bob.go_offline()
    net.run()
    assert net.roster() == {A: A1, B: A2, C: A3}

    bob.leave_network()
    assert bob.view().busy == "Leaving the network..."
    net.run()

    assert net.roster() == {A: A1, C: A3}
    assert bob.view().busy == ""
    assert bob.presence() is Presence.NONE
    assert bob.store.load(B) is None
    assert B not in net.world.lobbies[bob.view().lobby_id or next(iter(net.world.lobbies))].members


def test_coordinator_leaving_hands_over_a_roster_without_itself(net):
    three_members(net)
    assert net.owner() == A

    net.apps[A].leave_network()
    net.run()

    assert net.owner() in (B, C)
    assert net.roster() == {B: A2, C: A3}
    assert net.ping(B, C)


def test_owner_that_just_joined_hands_the_network_to_a_member(tmp_path):
    # Steam picks the newest member as owner: the one still showing its code.
    net = Net(tmp_path, pick_owner=lambda lobby: lobby.members[-1])
    alice = net.create(A)
    net.join(B, alice)
    code, lobby_id = alice.view().access_code, alice.view().lobby_id

    dave = net.start(D)
    dave.join(str(lobby_id), code)
    dave.tick()  # in the lobby, not admitted yet
    alice.go_offline()
    assert net.owner() == D
    net.run()

    assert net.owner() == B
    assert dave.presence() is Presence.ONLINE
    assert net.roster() == {A: A1, B: A2, D: A3}


def test_when_everyone_is_offline_the_network_is_gone(net):
    three_members(net)
    for steam_id in (B, C, A):
        net.stop(steam_id)
    assert net.world.lobbies == {}

    alice = net.start(A)
    net.run()

    assert alice.presence() is Presence.NONE
    assert alice.view().screen is Screen.HOME
    assert alice.error == NETWORK_GONE
    assert alice.store.load(A) is None


def test_a_member_alone_online_keeps_the_network(net):
    three_members(net)
    net.stop(A)
    net.stop(B)
    net.run()

    assert net.owner() == C
    alice = net.start(A)
    net.run()

    assert alice.presence() is Presence.ONLINE
    assert alice.network.local_address == A1


def test_stale_saved_network_with_a_reused_lobby_id_is_forgotten(net):
    alice = three_members(net)
    lobby = net.world.lobbies[alice.view().lobby_id]
    net.stop(A)
    lobby.data[access.NETWORK_ID_KEY] = "0" * access.NETWORK_ID_LENGTH

    alice = net.start(A)
    net.run()

    assert alice.presence() is Presence.NONE
    assert alice.error == NETWORK_GONE
    assert A not in lobby.members


def test_exit_cleans_up_everything(net):
    three_members(net)
    bob = net.apps[B]
    steam, helper = bob.steam, bob.helper

    net.stop(B)

    assert helper.state is State.STOPPED
    assert not steam.running
    assert steam.connections == {}
    assert steam.listen_sockets == set()
    assert all(B not in lobby.members for lobby in net.world.lobbies.values())
