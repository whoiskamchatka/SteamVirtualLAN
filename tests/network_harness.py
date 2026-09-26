"""Several PCs running SteamVirtualLAN on the simulated Steam in steam_sim.py.

Each PC is an AppController with its own saved state and a FakeHelper in
place of the adapter, so whole networks run without Steam, UAC or Wintun.
"""

from ipaddress import IPv4Address

from app_fakes import GUEST, HOST, OTHER, FakeHelper, ipv4_packet
from steam_sim import World

from steamlan.adapter.launcher import State
from steamlan.app import roster, tunnel
from steamlan.app.controller import AppController, Presence
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

    def start(self, steam_id, logged_on=True) -> AppController:
        """Start SteamVirtualLAN on steam_id's PC (again)."""
        steam = self.world.client(steam_id, NAMES.get(steam_id, ""))
        steam.logged_on = logged_on
        send = steam.send_message

        def recorded(handle, data, reliable=True):
            connection = steam.connections.get(handle)
            send(handle, data, reliable)
            self.wire.append((steam_id, connection.remote, bytes(data)))

        steam.send_message = recorded
        store = StateStore(self.tmp_path / str(steam_id) / "state.json")
        helpers = []

        def make_helper():
            helpers.append(FakeHelper(steam))
            return helpers[-1]

        app = AppController(lambda: steam, self.clock, make_helper, store)
        # Every helper this run of the app started, to catch doubles.
        app.helpers = helpers
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

    def run(self, ticks=8, step=0.05):
        for _ in range(ticks):
            for app in list(self.apps.values()):
                app.tick()
                if app.helper is not None and app.helper.state is State.STARTING:
                    app.helper.become_ready()
            self.clock.now += step

    def steam(self, steam_id):
        return self.apps[steam_id].steam

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


def three_members(net):
    alice = net.create(A)
    net.join(B, alice)
    net.join(C, alice)
    net.run()
    return alice
