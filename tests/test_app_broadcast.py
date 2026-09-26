"""Broadcast and multicast between whole PCs, on the simulated Steam (network_harness.py).

Four PCs: A (10.77.0.1), B (.2), C (.3) and D (.4). "Windows" on a PC is its
FakeHelper: from_windows is what Windows sent into the adapter, to_windows
what the app handed to Windows.
"""

import pytest
from app_fakes import udp_packet, udp_ports
from network_harness import A, B, C, D, Net, three_members

from steamlan.adapter.ipv4 import addressing
from steamlan.app import access, tunnel
from steamlan.app.controller import Presence

ADDRESS = {A: "10.77.0.1", B: "10.77.0.2", C: "10.77.0.3", D: "10.77.0.4"}
# Any ports: nothing about them is special to SteamVirtualLAN.
GAME_PORT = 47624


@pytest.fixture
def net(tmp_path):
    net = Net(tmp_path)
    alice = three_members(net)
    net.join(D, alice)
    net.run(4)
    assert all(app.presence() is Presence.ONLINE for app in net.apps.values())
    return net


def send(net, source, packet, ticks=3):
    """Windows on source sends packet into its adapter; returns how many
    times each PC's Windows got it."""
    net.wire.clear()
    net.apps[source].helper.from_windows.append(packet)
    net.run(ticks)
    return {
        steam_id: app.helper.to_windows.count(packet)
        for steam_id, app in net.apps.items()
        if app.helper is not None
    }


def carried(net, packet):
    """(from, to) of every message on the wire that carried packet."""
    return sorted(
        (sender, receiver)
        for sender, receiver, data in net.wire
        if tunnel.packet_payload(data) == packet
    )


def discovery(source, destination, data=b"LAN_DISCOVERY v1"):
    return udp_packet(
        ADDRESS[source] if source in ADDRESS else source, destination, 53555, GAME_PORT, data
    )


def test_unicast_goes_only_to_its_member(net):
    packet = discovery(A, "10.77.0.2")

    assert send(net, A, packet) == {A: 0, B: 1, C: 0, D: 0}
    assert carried(net, packet) == [(A, B)]


@pytest.mark.parametrize(
    "destination",
    ["10.77.0.255", "255.255.255.255", "239.255.255.250", "224.0.0.251"],
    ids=["network broadcast", "limited broadcast", "multicast", "link-local multicast"],
)
def test_broadcast_and_multicast_reach_every_other_member_once(net, destination):
    packet = discovery(B, destination)

    received = send(net, B, packet)

    assert received == {A: 1, B: 0, C: 1, D: 1}
    # One copy straight from the sender to each member; nobody passes it on.
    assert carried(net, packet) == [(B, A), (B, C), (B, D)]


@pytest.mark.parametrize("destination", ["10.77.0.255", "239.255.255.250"])
def test_packet_bytes_and_ports_are_unchanged_end_to_end(net, destination):
    packet = discovery(C, destination, data=b"\x00\x01game-specific\xffbytes")

    send(net, C, packet)

    for steam_id in (A, B, D):
        (delivered,) = net.apps[steam_id].helper.to_windows[-1:]
        assert delivered == packet
        assert udp_ports(delivered) == (53555, GAME_PORT, b"\x00\x01game-specific\xffbytes")
        info = addressing(delivered)
        assert (str(info.source), str(info.destination)) == (ADDRESS[C], destination)


def test_offline_members_get_nothing(net):
    net.apps[C].go_offline()
    net.run()
    packet = discovery(A, "255.255.255.255")

    received = send(net, A, packet)

    assert received == {A: 0, B: 1, D: 1}
    assert carried(net, packet) == [(A, B), (A, D)]


def test_members_that_are_reconnecting_get_nothing_until_connected(net):
    # D's connections drop while everyone stays in the lobby.
    net.steam(D)._drop_connections(notify_self=True)
    net.run(1)
    assert D not in net.apps[A].network.lobby.connected_peers()
    assert {m.steam_id: m.status for m in net.apps[A].view().members}[D] == "Reconnecting..."
    packet = discovery(A, "10.77.0.255")

    received = send(net, A, packet, ticks=1)

    assert received[D] == 0
    assert (A, D) not in carried(net, packet)


def test_a_member_that_reconnected_gets_one_copy(net):
    net.steam(D)._drop_connections(notify_self=True)
    # Reconnecting takes a few seconds; afterwards each member has one connection.
    net.run(140)
    packet = discovery(A, "239.255.255.250")

    received = send(net, A, packet)

    assert received == {A: 0, B: 1, C: 1, D: 1}
    assert carried(net, packet).count((A, D)) == 1


def test_received_broadcast_and_multicast_are_never_sent_on(net):
    for destination in ("10.77.0.255", "255.255.255.255", "239.255.255.250"):
        packet = discovery(A, destination)
        send(net, A, packet)
        before = {steam_id: net.apps[steam_id].network.traffic.snapshot() for steam_id in net.apps}

        # Even if Windows on B handed it straight back to the adapter, B
        # doesn't send it anywhere: it isn't from B's own address.
        net.wire.clear()
        net.apps[B].helper.from_windows.append(packet)
        net.run(3)

        assert carried(net, packet) == []
        dropped = net.apps[B].network.traffic.snapshot()["dropped"]
        assert dropped["tx_unroutable"] == before[B]["dropped"].get("tx_unroutable", 0) + 1
        for steam_id in (A, C, D):
            assert net.apps[steam_id].network.traffic.snapshot()["rx"] == before[steam_id]["rx"]


def test_a_member_cannot_claim_another_members_address(net):
    # Windows on B sends a broadcast claiming to be from A: B's app drops it.
    spoofed = discovery(A, "10.77.0.255")
    assert send(net, B, spoofed) == {A: 0, B: 0, C: 0, D: 0}
    assert carried(net, spoofed) == []

    # A modified app on B sends it anyway: C and D's apps drop it.
    steam = net.steam(B)
    for handle, connection in list(steam.connections.items()):
        if connection.connected:
            steam.send_message(handle, tunnel.packet_message(spoofed), reliable=False)
    net.run(3)

    for steam_id in (A, C, D):
        assert net.apps[steam_id].helper.to_windows.count(spoofed) == 0
        assert net.apps[steam_id].network.traffic.snapshot()["dropped"]["rx_rejected"] >= 1


def test_counters(net):
    before = {steam_id: app.network.traffic.snapshot() for steam_id, app in net.apps.items()}

    send(net, B, discovery(B, "10.77.0.255"))
    send(net, B, discovery(B, "239.255.255.250"))
    send(net, B, discovery(B, "10.77.0.3"))
    send(net, B, discovery(B, "10.77.0.99"))  # nobody has it
    send(net, B, discovery(B, "8.8.8.8"))  # outside the network

    def change(steam_id, key):
        after = net.apps[steam_id].network.traffic.snapshot()[key]
        if isinstance(after, dict):
            return {k: v - before[steam_id][key].get(k, 0) for k, v in after.items() if v}
        return after - before[steam_id][key]

    assert change(B, "tx") == {"unicast": 1, "broadcast": 1, "multicast": 1}
    assert change(B, "tx_copies") == 3 + 3 + 1
    assert change(B, "dropped") == {"tx_unknown_destination": 1, "tx_unroutable": 1}
    assert change(C, "rx") == {"unicast": 1, "broadcast": 1, "multicast": 1}
    assert change(A, "rx") == {"broadcast": 1, "multicast": 1}
    assert change(D, "rx") == {"broadcast": 1, "multicast": 1}


def test_control_messages_never_reach_windows(net):
    # Minutes of PINGs, PONGs and roster traffic.
    net.run(200, step=1.0)

    for app in net.apps.values():
        for packet in app.helper.to_windows:
            assert addressing(packet) is not None
            assert not packet.startswith(access.PREFIX)
        assert app.network.traffic.snapshot()["dropped"].get("rx_rejected", 0) == 0
