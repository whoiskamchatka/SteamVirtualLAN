"""The elevated network helper, with a fake adapter and a fake pipe: no Administrator needed."""

import threading
from ipaddress import IPv4Address
from unittest import mock

import pytest
from adapter_fakes import (
    FakeAdapter,
    FakeConnection,
    buffer_full,
    ipv4_packet,
    packet,
    set_address,
    wait_for,
)

from steamlan.adapter import helper as helper_module
from steamlan.adapter import protocol
from steamlan.adapter.adapter import AdapterError
from steamlan.adapter.helper import Channel, Helper, HelperError, may_write, serve
from steamlan.adapter.wintun import WintunLoadError

A1 = "10.77.0.1"
A2 = "10.77.0.2"
A3 = "10.77.0.3"


@pytest.fixture(autouse=True)
def quick_reader(monkeypatch):
    # The reader thread checks for stop this often; keep the tests fast.
    monkeypatch.setattr(helper_module, "READ_WAIT_MS", 10)


class Assign:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def __call__(self, luid, interface):
        self.calls.append((luid, interface))
        return self.result


def run_helper(*messages, adapter=None, assign=None):
    """Run a helper over messages until the app closes the pipe; returns what it sent."""
    connection = FakeConnection(*messages)
    channel = Channel(connection)
    helper = Helper(connection, channel, adapter or FakeAdapter(), assign or Assign())
    result = helper.run()
    channel.close()
    return result, connection, helper


def test_ready_first_then_stops_when_the_app_closes_the_pipe():
    result, connection, _ = run_helper()

    assert result == 0
    assert connection.tags() == [protocol.READY]


def test_stop_message_ends_the_helper():
    connection = FakeConnection(protocol.STOP, packet(b"never read"), finished=False)
    channel = Channel(connection)

    assert Helper(connection, channel, FakeAdapter(), Assign()).run() == 0
    assert connection.incoming.qsize() == 1


def test_set_address_assigns_it_once():
    assign = Assign()

    _, connection, helper = run_helper(set_address(A2), set_address(A2), assign=assign)

    assert assign.calls == [(FakeAdapter.luid, "10.77.0.2/24")]
    assert helper.address == IPv4Address(A2)
    assert connection.of(protocol.ADDRESS_SET) == [IPv4Address(A2).packed] * 2


def test_a_second_different_address_is_refused():
    connection = FakeConnection()
    helper = Helper(connection, Channel(connection), FakeAdapter(), Assign())
    helper.handle(set_address(A2))

    with pytest.raises(HelperError, match="already has the address 10.77.0.2"):
        helper.handle(set_address(A3))


@pytest.mark.parametrize(
    "body",
    [
        IPv4Address("192.168.1.2").packed,
        IPv4Address("10.77.0.0").packed,
        IPv4Address("10.77.0.255").packed,
        b"\x0a\x4d\x00",
        b"",
    ],
    ids=["outside", "network", "broadcast", "short", "empty"],
)
def test_only_addresses_in_the_virtual_network_are_assigned(body):
    assign = Assign()
    connection = FakeConnection()
    helper = Helper(connection, Channel(connection), FakeAdapter(), assign)

    with pytest.raises(ValueError):
        helper.handle(protocol.SET_ADDRESS + body)
    assert assign.calls == []


def test_packets_for_this_adapter_are_written_to_windows():
    adapter = FakeAdapter()
    reply = ipv4_packet(A1, A2, b"reply")

    run_helper(set_address(A2), packet(reply), adapter=adapter)

    assert adapter.written == [reply]


def test_packets_before_the_address_is_set_are_dropped():
    adapter = FakeAdapter()

    _, _, helper = run_helper(packet(ipv4_packet(A1, A2)), adapter=adapter)

    assert adapter.written == []
    assert helper.dropped_packets == 1


@pytest.mark.parametrize(
    "data",
    [
        ipv4_packet(A1, A3),  # for another member
        ipv4_packet(A2, A2),  # claims to come from this adapter
        ipv4_packet("192.168.1.1", A2),  # from outside the network
        ipv4_packet("10.77.0.255", A2),
        ipv4_packet(A1, "10.77.0.255"),
        ipv4_packet(A1, "224.0.0.251"),
        ipv4_packet(A1, A2)[:12],
        b"\x60" + bytes(39),
        b"",
    ],
    ids=[
        "other member",
        "own source",
        "outside",
        "broadcast source",
        "broadcast",
        "multicast",
        "truncated",
        "ipv6",
        "empty",
    ],
)
def test_the_helper_writes_nothing_else_to_windows(data):
    adapter = FakeAdapter()

    _, _, helper = run_helper(set_address(A2), packet(data), adapter=adapter)

    assert adapter.written == []
    assert helper.dropped_packets == 1
    assert not may_write(data, IPv4Address(A2))


def test_full_adapter_drops_the_packet():
    adapter = FakeAdapter()
    adapter.write_error = buffer_full()

    result, _, helper = run_helper(set_address(A2), packet(ipv4_packet(A1, A2)), adapter=adapter)

    assert result == 0
    assert helper.dropped_packets == 1


def test_other_write_errors_stop_the_helper():
    adapter = FakeAdapter()
    adapter.write_error = AdapterError("WintunAllocateSendPacket failed", 38)

    with pytest.raises(AdapterError):
        run_helper(set_address(A2), packet(ipv4_packet(A1, A2)), adapter=adapter)


def test_unknown_messages_are_ignored():
    result, connection, _ = run_helper(b"Z whatever", b"")

    assert result == 0


def test_packets_from_windows_go_to_the_app_once_the_address_is_set():
    adapter = FakeAdapter()
    connection = FakeConnection(finished=False)
    channel = Channel(connection)
    helper = Helper(connection, channel, adapter, Assign())
    thread = threading.Thread(target=helper.run)
    thread.start()
    request = ipv4_packet(A2, A1, b"request")

    # Before the address is known nothing is passed on.
    adapter.arrive(ipv4_packet(A2, A1, b"too early"))
    wait_for(lambda: adapter.pending() == 0)  # the reader took it and dropped it
    connection.give(set_address(A2))
    wait_for(lambda: connection.of(protocol.ADDRESS_SET))
    adapter.arrive(request, b"\x60" + bytes(39))
    wait_for(lambda: connection.of(protocol.PACKET))

    connection.finish()
    thread.join(5)
    channel.close()
    assert not thread.is_alive()
    assert connection.of(protocol.PACKET) == [request]


def test_read_errors_are_reported_to_the_app():
    adapter = FakeAdapter()
    adapter.read_error = AdapterError("WintunReceivePacket failed", 38)
    connection = FakeConnection(finished=False)
    channel = Channel(connection)
    thread = threading.Thread(target=Helper(connection, channel, adapter, Assign()).run)
    thread.start()

    adapter.arrive(b"x")
    wait_for(lambda: connection.of(protocol.ERROR))
    connection.finish()
    thread.join(5)

    assert b"the adapter stopped working" in connection.of(protocol.ERROR)[0]


def test_channel_drops_packets_when_the_app_does_not_keep_up(monkeypatch):
    monkeypatch.setattr(helper_module, "MAX_QUEUED_PACKETS", 2)
    blocked = threading.Event()

    class SlowConnection(FakeConnection):
        def send_bytes(self, message):
            blocked.wait(5)
            super().send_bytes(message)

    connection = SlowConnection()
    channel = Channel(connection)
    for number in range(5):
        channel.send_packet(bytes([number]))
    channel.send(protocol.LOG + b"control messages are never dropped")
    blocked.set()
    channel.close()

    assert channel.dropped_packets >= 2
    assert connection.of(protocol.LOG) == [b"control messages are never dropped"]


def test_serve_creates_and_removes_the_adapter(caplog):
    caplog.set_level("INFO")
    adapter = FakeAdapter()
    connection = FakeConnection(set_address(A1), protocol.STOP)
    channel = Channel(connection)

    result = serve(
        connection, channel, "wintun.dll", lambda path: mock.Mock(), lambda w: adapter, Assign()
    )
    channel.close()

    assert result == 0
    assert adapter.events == ["open", "start", "close"]
    assert connection.tags()[:2] == [protocol.READY, protocol.ADDRESS_SET]
    assert "Removed the adapter" in caplog.messages


@pytest.mark.parametrize(
    ("load", "adapter", "message"),
    [
        (WintunLoadError("not the official Wintun"), None, b"not the official Wintun"),
        (None, AdapterError("WintunCreateAdapter failed: Access is denied"), b"Access is denied"),
    ],
    ids=["unverified dll", "adapter not created"],
)
def test_serve_reports_startup_failures(load, adapter, message):
    fake_adapter = FakeAdapter(fail_open=adapter)
    connection = FakeConnection()
    channel = Channel(connection)

    def load_wintun(path):
        if load:
            raise load
        return mock.Mock()

    result = serve(connection, channel, "wintun.dll", load_wintun, lambda w: fake_adapter, Assign())
    channel.close()

    assert result == 1
    (error,) = connection.of(protocol.ERROR)
    assert message in error
    assert protocol.READY not in connection.tags()
    # Whatever was opened is closed again.
    assert fake_adapter.events == ([] if load else ["open", "close"])


def test_serve_removes_the_adapter_after_a_bad_request():
    adapter = FakeAdapter()
    connection = FakeConnection(protocol.SET_ADDRESS + b"\x01\x02\x03\x04")
    channel = Channel(connection)

    result = serve(
        connection, channel, "wintun.dll", lambda path: mock.Mock(), lambda w: adapter, Assign()
    )
    channel.close()

    assert result == 1
    assert adapter.events == ["open", "start", "close"]
    assert connection.of(protocol.ERROR)


def test_main_only_connects_to_a_local_pipe(monkeypatch):
    monkeypatch.setattr(helper_module, "Client", lambda *a, **k: pytest.fail("connected"))

    arguments = ["--pipe", "\\\\server\\pipe\\x", "--key", "00", "--wintun", "wintun.dll"]
    assert helper_module.main(arguments) == 2


def test_main_changes_nothing_when_it_cannot_connect(monkeypatch):
    monkeypatch.setattr(helper_module, "serve", lambda *a: pytest.fail("served"))
    arguments = [
        "--pipe",
        "\\\\.\\pipe\\steamvirtuallan-test-no-such-pipe",
        "--key",
        "00" * 32,
        "--wintun",
        "wintun.dll",
    ]

    assert helper_module.main(arguments) == 2
