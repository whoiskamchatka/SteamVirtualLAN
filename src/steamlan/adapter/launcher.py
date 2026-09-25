"""Starting the network helper from the unelevated app, and talking to it.

The app itself, with Steam and the window, never runs as Administrator. For
the adapter it starts the helper (helper.py) through Windows' UAC prompt and
exchanges messages with it over a named pipe that only the two of them use
(protocol.py).
"""

import enum
import logging
import secrets
import sys
import threading
import time
from collections.abc import Callable
from ipaddress import IPv4Address
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from pathlib import Path

from steamlan.adapter import protocol
from steamlan.adapter.adapter import ADAPTER_NAME, AdapterError
from steamlan.adapter.bootstrap import ensure_wintun
from steamlan.adapter.windows import Process, is_admin, start_process
from steamlan.adapter.wintun import WintunLoadError

log = logging.getLogger(__name__)
helper_log = logging.getLogger("steamlan.helper")

# From the UAC prompt being accepted until the helper reports a running
# adapter. Installing Wintun's driver the first time takes a few seconds.
START_TIMEOUT = 60.0
# How long leaving waits for the helper to remove the adapter and exit.
STOP_TIMEOUT = 5.0
# Messages handled per poll(), so a flood of packets can't stall the window.
MAX_MESSAGES_PER_POLL = 512

UAC_DECLINED = (
    "SteamVirtualLAN needs Administrator permission to create its network adapter. "
    "The permission was not given, so the virtual network was not started."
)


class State(enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    STOPPED = "stopped"


def helper_command() -> list[str]:
    """How to run the helper: the same program as the app, with HELPER_FLAG."""
    if getattr(sys, "frozen", False):
        return [sys.executable, protocol.HELPER_FLAG]
    return [sys.executable, "-m", "steamlan", protocol.HELPER_FLAG]


def launch_helper(arguments: list[str]) -> Process | None:
    """Start the helper elevated; None if the UAC prompt was declined."""
    executable, *rest = helper_command()
    return start_process(executable, [*rest, *arguments], elevate=not is_admin())


class AdapterHelper:
    """The app's side of the helper that owns the virtual adapter.

    start() returns at once. Getting wintun.dll, the UAC prompt and waiting for
    the helper to connect happen on a worker thread; everything else happens
    on the caller's thread, from poll(), which the window calls regularly.
    """

    def __init__(
        self,
        prepare: Callable[..., Path] = ensure_wintun,
        launch: Callable[[list[str]], Process | None] = launch_helper,
        clock: Callable[[], float] = time.monotonic,
        stop_timeout: float = STOP_TIMEOUT,
    ):
        self._prepare = prepare
        self._launch = launch
        self.clock = clock
        self.stop_timeout = stop_timeout
        self.state = State.IDLE
        self.progress = ""
        self.error = ""
        # The address the helper confirmed the adapter has.
        self.address: IPv4Address | None = None
        self._requested: IPv4Address | None = None
        self._lock = threading.Lock()
        self._closing = False
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._process: Process | None = None
        self._connection = None
        self._launched_at = 0.0

    @property
    def ready(self) -> bool:
        return self.state is State.READY

    def start(self) -> None:
        if self.state is not State.IDLE:
            return
        key = secrets.token_bytes(32)
        # A new pipe with a random name; only the first instance may use it.
        self._listener = Listener(family="AF_PIPE", authkey=key)
        self.state = State.STARTING
        self.progress = "Starting the virtual network..."
        self._thread = threading.Thread(
            target=self._start, args=(self._listener, key), name="adapter-helper", daemon=True
        )
        self._thread.start()

    def poll(self) -> list[bytes]:
        """Handle what the helper sent; returns the IPv4 packets Windows sent
        into the adapter."""
        if self.state not in (State.STARTING, State.READY):
            return []
        connection = self._connection
        if connection is None:
            self._check_start()
            return []

        packets = []
        try:
            for _ in range(MAX_MESSAGES_PER_POLL):
                if not connection.poll():
                    break
                tag, body = protocol.split(connection.recv_bytes(protocol.MAX_MESSAGE))
                if tag == protocol.PACKET:
                    if self.address is not None:
                        packets.append(body)
                elif tag == protocol.LOG:
                    helper_log.info("%s", protocol.text(body))
                elif tag == protocol.READY:
                    self.state = State.READY
                    self.progress = ""
                    log.info("The %s adapter is running", ADAPTER_NAME)
                elif tag == protocol.ADDRESS_SET:
                    self.address = protocol.parse_address(body)
                elif tag == protocol.ERROR:
                    self._fail(f"The virtual network adapter failed: {protocol.text(body)}")
                    break
        except (EOFError, OSError, ValueError) as exc:
            log.warning("Network helper: %s", exc)
            self._fail("The virtual network helper stopped unexpectedly")
        if self.state is State.STARTING:
            self._check_start()
        return packets

    def set_address(self, address: IPv4Address) -> None:
        """Give the adapter its address; the helper confirms it through poll()."""
        if self.ready and address != self._requested:
            self._requested = address
            self._send(protocol.SET_ADDRESS + address.packed)

    def send_packet(self, packet: bytes) -> None:
        """Hand an IPv4 packet from another member to Windows."""
        if self.ready and self.address is not None:
            self._send(protocol.PACKET + packet)

    def close(self) -> None:
        """Stop the helper and wait until it has removed the adapter.

        Safe at any point: while the UAC prompt is open, while the helper is
        starting, after it failed, and more than once.
        """
        with self._lock:
            if self._closing:
                return
            self._closing = True
            listener, self._listener = self._listener, None
            connection, self._connection = self._connection, None
            process, self._process = self._process, None
        if self.state in (State.STARTING, State.READY):
            self.state = State.STOPPED

        if connection is not None:
            self._stop_connected(connection)
        elif listener is not None and self._thread is not None and self._thread.is_alive():
            # Wake a worker thread waiting for the helper to connect, so it ends.
            # A helper that shows up later finds no pipe and exits unchanged.
            try:
                Client(listener.address, family="AF_PIPE").close()
            except OSError:
                pass
        if listener is not None:
            listener.close()
        if process is not None:
            if process.wait(self.stop_timeout) is None:
                log.warning("The network helper has not exited yet")
            process.close()

    def _stop_connected(self, connection) -> None:
        """Ask the helper to stop, and read until it closes the pipe, which it
        does after removing the adapter."""
        deadline = self.clock() + self.stop_timeout
        try:
            connection.send_bytes(protocol.STOP)
            while self.clock() < deadline:
                if connection.poll(0.1):
                    tag, body = protocol.split(connection.recv_bytes(protocol.MAX_MESSAGE))
                    if tag == protocol.LOG:
                        helper_log.info("%s", protocol.text(body))
        except (EOFError, OSError):
            pass  # the helper has closed its end
        else:
            log.warning("The network helper did not stop in time")
        connection.close()

    def _start(self, listener: Listener, key: bytes) -> None:
        """Worker thread: get wintun.dll, start the helper, wait for it to connect."""
        try:
            dll = self._prepare(progress=self._set_progress)
            self._set_progress("Waiting for Administrator permission...")
            process = self._launch(
                ["--pipe", listener.address, "--key", key.hex(), "--wintun", str(dll)]
            )
            if process is None:
                self._fail(UAC_DECLINED)
                return
            with self._lock:
                if self._closing:
                    process.close()
                    return
                self._process = process
                self._launched_at = self.clock()
            self._set_progress(f"Creating the {ADAPTER_NAME} adapter...")
            connection = listener.accept()
        except WintunLoadError as exc:
            self._fail(f"The networking component could not be prepared: {exc}")
            return
        except (AdapterError, OSError, EOFError, AuthenticationError) as exc:
            if not self._closing:
                log.warning("Network helper: %s", exc)
                self._fail("The virtual network helper could not be started")
            return
        with self._lock:
            if self._closing:
                connection.close()
                return
            self._connection = connection

    def _check_start(self) -> None:
        """Fail if the helper exited or is taking too long before it is ready."""
        process = self._process
        if process is None:
            return
        code = process.poll()
        if code is not None and self._connection is None:
            self._fail(f"The virtual network helper stopped before it was ready (exit code {code})")
        elif self.clock() - self._launched_at > START_TIMEOUT:
            self._fail("The virtual network adapter did not start in time")

    def _send(self, message: bytes) -> None:
        try:
            self._connection.send_bytes(message)
        except OSError as exc:
            log.warning("Network helper: %s", exc)
            self._fail("The virtual network helper stopped unexpectedly")

    def _set_progress(self, text: str) -> None:
        if self.state is State.STARTING:
            self.progress = text

    def _fail(self, message: str) -> None:
        if self._closing or self.state is State.FAILED:
            return
        log.warning("%s", message)
        self.state = State.FAILED
        self.error = message
        self.progress = ""
