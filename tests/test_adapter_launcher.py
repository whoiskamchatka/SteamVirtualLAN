"""The app's side of the network helper, over a real named pipe.

The helper side runs in a thread with a fake adapter instead of in an elevated
process, so nothing here needs Administrator rights or shows a UAC prompt.
"""

import sys
import threading
import time
from ipaddress import IPv4Address
from multiprocessing.connection import Client
from pathlib import Path
from unittest import mock

import pytest
from adapter_fakes import FakeAdapter, ipv4_packet

from steamlan.adapter import helper as helper_module
from steamlan.adapter import launcher, protocol
from steamlan.adapter.helper import Channel, serve
from steamlan.adapter.launcher import UAC_DECLINED, AdapterHelper, State
from steamlan.adapter.wintun import WintunLoadError

A1 = IPv4Address("10.77.0.1")
A2 = IPv4Address("10.77.0.2")
DLL = Path("wintun/bin/amd64/wintun.dll")


@pytest.fixture(autouse=True)
def quick_reader(monkeypatch):
    monkeypatch.setattr(helper_module, "READ_WAIT_MS", 10)


def prepare(progress):
    progress("Downloading networking component...")
    return DLL


def options(arguments):
    return dict(zip(arguments[::2], arguments[1::2], strict=True))


class HelperThread:
    """Launches the helper side in a thread, the way the elevated process runs it."""

    def __init__(self, adapter=None, key=None, fail_connect=False):
        self.adapter = adapter or FakeAdapter()
        self.key = key
        self.fail_connect = fail_connect
        self.assigned = []
        self.arguments = None
        self.code = None
        self.thread = None

    def __call__(self, arguments):
        self.arguments = arguments
        self.thread = threading.Thread(target=self._run, args=(options(arguments),))
        self.thread.start()
        return self

    def _run(self, given):
        key = self.key or bytes.fromhex(given["--key"])
        try:
            connection = Client(given["--pipe"], family="AF_PIPE", authkey=key)
        except Exception:
            self.code = 2
            return
        self.connection = connection
        channel = Channel(connection)
        try:
            self.code = serve(
                connection,
                channel,
                Path(given["--wintun"]),
                lambda path: mock.Mock(),
                lambda wintun: self.adapter,
                lambda luid, interface: self.assigned.append(interface) or True,
            )
        finally:
            channel.close()
            connection.close()

    # The Process interface
    def poll(self):
        return None if self.thread.is_alive() else self.code

    def wait(self, timeout):
        self.thread.join(timeout)
        return self.poll()

    def close(self):
        pass


def until(helper, condition, received=None, timeout=5.0):
    """Poll like the window does until condition(); packets polled go to received."""
    received = [] if received is None else received
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out in state {helper.state}: {helper.error}")
        received += helper.poll()
        time.sleep(0.005)
    return received


def started(launch, **kwargs):
    helper = AdapterHelper(prepare, launch, **kwargs)
    helper.start()
    return helper


def test_packets_cross_the_pipe_both_ways():
    launch = HelperThread()
    helper = started(launch)
    until(helper, lambda: helper.ready)
    assert options(launch.arguments)["--wintun"] == str(DLL)
    assert launch.arguments[1].startswith("\\\\.\\pipe\\")

    helper.set_address(A2)
    helper.set_address(A2)
    until(helper, lambda: helper.address == A2)
    assert launch.assigned == ["10.77.0.2/24"]

    # Another member -> app -> helper -> adapter -> Windows
    request = ipv4_packet(A1, A2, b"echo request")
    helper.send_packet(request)
    until(helper, lambda: launch.adapter.written)
    assert launch.adapter.written == [request]

    # Windows -> adapter -> helper -> app
    reply = ipv4_packet(A2, A1, b"echo reply")
    launch.adapter.arrive(reply)
    received = []
    until(helper, lambda: reply in received, received)
    assert received == [reply]

    helper.close()
    assert helper.state is State.STOPPED
    # close() returns only once the helper has removed the adapter and exited.
    assert launch.adapter.events == ["open", "start", "close"]
    assert launch.code == 0


def test_nothing_is_sent_before_the_helper_is_ready():
    release = threading.Event()

    class SlowAdapter(FakeAdapter):
        def open(self):
            release.wait(5)
            super().open()

    launch = HelperThread(SlowAdapter())
    helper = started(launch)
    until(helper, lambda: helper._connection is not None)

    helper.set_address(A2)
    helper.send_packet(ipv4_packet(A1, A2))
    release.set()
    until(helper, lambda: helper.ready)
    helper.close()

    assert launch.assigned == []
    assert launch.adapter.written == []


def test_declined_uac_prompt():
    helper = started(lambda arguments: None)

    until(helper, lambda: helper.state is State.FAILED)

    assert helper.error == UAC_DECLINED
    helper.close()
    assert helper.state is State.FAILED


def test_progress_while_starting():
    release = threading.Event()

    def launch(arguments):
        release.wait(5)

    helper = started(launch)
    until(helper, lambda: helper.progress == "Waiting for Administrator permission...")
    release.set()
    until(helper, lambda: helper.state is State.FAILED)
    helper.close()


def test_wintun_that_cannot_be_prepared_is_never_launched():
    def prepare(progress):
        raise WintunLoadError("could not download")

    helper = AdapterHelper(prepare, lambda arguments: pytest.fail("launched"))
    helper.start()

    until(helper, lambda: helper.state is State.FAILED)
    assert "could not download" in helper.error
    helper.close()


def test_helper_that_exits_before_connecting():
    class Exited:
        def poll(self):
            return 2

        def wait(self, timeout):
            return 2

        def close(self):
            self.closed = True

    process = Exited()
    helper = started(lambda arguments: process)

    until(helper, lambda: helper.state is State.FAILED)
    assert helper.error == "The virtual network helper stopped before it was ready (exit code 2)"

    helper.close()
    helper._thread.join(5)
    assert not helper._thread.is_alive()
    assert process.closed


def test_helper_with_the_wrong_key_is_not_accepted():
    launch = HelperThread(key=b"\x00" * 32)
    helper = started(launch)

    until(helper, lambda: helper.state is State.FAILED)
    helper.close()

    assert launch.adapter.events == []
    assert launch.code == 2


def test_helper_that_does_not_start_in_time():
    clock = mock.Mock(return_value=0.0)

    class Hanging:
        def poll(self):
            return None

        def wait(self, timeout):
            return None

        def close(self):
            pass

    helper = AdapterHelper(prepare, lambda arguments: Hanging(), clock=clock, stop_timeout=0)
    helper.start()
    until(helper, lambda: helper.progress.startswith("Creating"))

    clock.return_value = launcher.START_TIMEOUT + 1
    helper.poll()

    assert helper.state is State.FAILED
    assert helper.error == "The virtual network adapter did not start in time"
    helper.close()
    helper._thread.join(5)
    assert not helper._thread.is_alive()


def test_adapter_that_cannot_be_created():
    launch = HelperThread(FakeAdapter(fail_open=launcher.AdapterError("Access is denied")))
    helper = started(launch)

    until(helper, lambda: helper.state is State.FAILED)
    assert helper.error == "The virtual network adapter failed: Access is denied"
    helper.close()
    assert launch.code == 1
    assert launch.adapter.events == ["open", "close"]


def test_adapter_that_stops_working_while_running():
    launch = HelperThread()
    helper = started(launch)
    until(helper, lambda: helper.ready)

    launch.adapter.read_error = launcher.AdapterError("adapter gone", 38)
    launch.adapter.arrive(b"x")

    until(helper, lambda: helper.state is State.FAILED)
    assert "adapter gone" in helper.error
    helper.close()
    assert launch.adapter.events[-1] == "close"


def test_helper_that_crashes_while_running():
    launch = HelperThread()
    helper = started(launch)
    until(helper, lambda: helper.ready)

    launch.connection.close()  # as when the process dies

    until(helper, lambda: helper.state is State.FAILED)
    assert helper.error == "The virtual network helper stopped unexpectedly"
    helper.close()


def test_close_while_the_uac_prompt_is_open():
    release = threading.Event()
    late = HelperThread()

    def launch(arguments):
        release.wait(5)  # the user is looking at the UAC prompt
        return late(arguments)

    helper = started(launch)
    until(helper, lambda: helper.progress == "Waiting for Administrator permission...")

    helper.close()
    assert helper.state is State.STOPPED
    # The prompt is accepted only now: the helper finds no pipe and changes nothing.
    release.set()
    helper._thread.join(5)
    late.thread.join(5)
    assert late.code == 2
    assert late.adapter.events == []


def test_close_twice_and_before_start():
    AdapterHelper(prepare, lambda arguments: None).close()
    launch = HelperThread()
    helper = started(launch)
    until(helper, lambda: helper.ready)

    helper.close()
    helper.close()

    assert launch.adapter.events == ["open", "start", "close"]


def test_helper_command(monkeypatch):
    assert launcher.helper_command() == [sys.executable, "-m", "steamlan", "--network-helper"]

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert launcher.helper_command() == [sys.executable, "--network-helper"]


@pytest.mark.parametrize(("admin", "elevate"), [(False, True), (True, False)])
def test_launch_asks_for_elevation_only_when_needed(monkeypatch, admin, elevate):
    started_with = []
    monkeypatch.setattr(launcher, "is_admin", lambda: admin)
    monkeypatch.setattr(
        launcher,
        "start_process",
        lambda executable, arguments, elevate: started_with.append((arguments, elevate)),
    )

    launcher.launch_helper(["--pipe", "p"])

    assert started_with == [(["-m", "steamlan", protocol.HELPER_FLAG, "--pipe", "p"], elevate)]
