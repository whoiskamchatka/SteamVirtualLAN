"""The network helper: the only part of SteamVirtualLAN that runs as Administrator.

The app starts it through Windows' UAC prompt as
"python -m steamlan --network-helper --pipe ... --key ... --wintun ...". It
connects back to the app's pipe, loads the verified wintun.dll, creates the
SteamVirtualLAN adapter and then does nothing but:

- give the adapter the one address the app asks for, which must be in 10.77.0.0/24;
- pass the IPv4 packets Windows sends into the adapter to the app;
- write IPv4 packets from the app to the adapter, if they come from another
  address in the network and are addressed to the adapter's own address,
  the network's broadcast address or a multicast group.

When the app says stop, or its end of the pipe closes because it exited or
crashed, the helper removes the adapter and exits.
"""

import argparse
import ipaddress
import logging
import queue
import threading
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client
from pathlib import Path

from steamlan.adapter import protocol
from steamlan.adapter.adapter import AdapterError, VirtualAdapter
from steamlan.adapter.ipv4 import ip_version, may_deliver
from steamlan.adapter.windows import assign_ipv4
from steamlan.adapter.wintun import (
    ERROR_BUFFER_OVERFLOW,
    WintunLoadError,
    forward_log,
    load_wintun,
)

log = logging.getLogger("steamlan.helper")

# Packets from Windows that may wait for the pipe; more are dropped, as on a
# full link.
MAX_QUEUED_PACKETS = 1024
READ_WAIT_MS = 250
_LOCAL_PIPE = "\\\\.\\pipe\\"


class HelperError(Exception):
    pass


def may_write(packet: bytes, address: ipaddress.IPv4Address) -> bool:
    """Whether a packet from the app may go to Windows: IPv4 from another
    address in the network, to this adapter's own address, the network's
    broadcast address or a multicast group (ipv4.may_deliver). The app checks
    this too; the helper doesn't rely on it."""
    return may_deliver(packet, address) is not None


class Channel:
    """Sends to the app from a writer thread of its own.

    If the pipe to the app is full, only that thread waits; the helper keeps
    reading what the app sends, so the two can never wait for each other.
    """

    def __init__(self, connection):
        self.connection = connection
        self.dropped_packets = 0
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._thread = threading.Thread(target=self._write, name="helper-writer", daemon=True)
        self._thread.start()

    def send(self, message: bytes) -> None:
        self._queue.put(message)

    def send_packet(self, packet: bytes) -> None:
        if self._queue.qsize() >= MAX_QUEUED_PACKETS:
            self.dropped_packets += 1
            return
        self._queue.put(protocol.PACKET + packet)

    def close(self, timeout: float = 2.0) -> None:
        """Send what is still queued, then stop."""
        self._queue.put(None)
        self._thread.join(timeout)

    def _write(self) -> None:
        while (message := self._queue.get()) is not None:
            try:
                self.connection.send_bytes(message)
            except OSError:
                return  # the app is gone


class PipeLogHandler(logging.Handler):
    """Log lines go to the app, which logs them with its own."""

    def __init__(self, channel: Channel):
        super().__init__()
        self.channel = channel

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.name != log.name:
                text = f"{record.name}: {text}"  # e.g. Wintun's own messages
            self.channel.send(protocol.LOG + text.encode())
        except Exception:
            self.handleError(record)


class Helper:
    """Serves the app with an adapter that is already open and started."""

    def __init__(self, connection, channel: Channel, adapter: VirtualAdapter, assign=assign_ipv4):
        self.connection = connection
        self.channel = channel
        self.adapter = adapter
        self.assign = assign
        self.address: ipaddress.IPv4Address | None = None
        self.dropped_packets = 0
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None

    def run(self) -> int:
        """Handle the app's messages until it says stop or goes away."""
        self._reader = threading.Thread(target=self._read, name="helper-reader", daemon=True)
        self._reader.start()
        self.channel.send(protocol.READY)
        try:
            while True:
                try:
                    message = self.connection.recv_bytes(protocol.MAX_MESSAGE)
                except (EOFError, OSError):
                    log.info("SteamVirtualLAN closed its connection")
                    return 0
                if not self.handle(message):
                    return 0
        finally:
            # Wintun's session must not be used by the reader once it ends.
            self._stop.set()
            self._reader.join()

    def handle(self, message: bytes) -> bool:
        """Carry out one message; False when the helper should stop."""
        tag, body = protocol.split(message)
        if tag == protocol.PACKET:
            self.write_packet(body)
        elif tag == protocol.SET_ADDRESS:
            self.set_address(protocol.parse_address(body))
        elif tag == protocol.STOP:
            log.info("Stopping")
            return False
        else:
            log.warning("Ignored an unknown message %r", tag)
        return True

    def set_address(self, address: ipaddress.IPv4Address) -> None:
        if self.address is None:
            interface = f"{address}/{protocol.PREFIX_LENGTH}"
            added = self.assign(self.adapter.luid, interface)
            self.address = address
            log.info(
                "%s %s on %s only", "Assigned" if added else "Kept", interface, self.adapter.name
            )
        elif address != self.address:
            raise HelperError(f"the adapter already has the address {self.address}")
        self.channel.send(protocol.ADDRESS_SET + address.packed)

    def write_packet(self, packet: bytes) -> None:
        if self.address is None or not may_write(packet, self.address):
            self.dropped_packets += 1
            return
        try:
            self.adapter.write(packet)
        except AdapterError as exc:
            if exc.winerror != ERROR_BUFFER_OVERFLOW:
                raise
            # Windows isn't taking packets as fast as they come.
            self.dropped_packets += 1

    def _read(self) -> None:
        try:
            while not self._stop.is_set():
                if not self.adapter.wait(READ_WAIT_MS):
                    continue
                while not self._stop.is_set() and (packet := self.adapter.read()) is not None:
                    # Windows sends IPv6 too; only IPv4 is carried.
                    if self.address is not None and ip_version(packet) == 4:
                        self.channel.send_packet(packet)
        except AdapterError as exc:
            log.error("Reading from the adapter failed: %s", exc)
            self.channel.send(protocol.ERROR + f"the adapter stopped working: {exc}".encode())


def serve(
    connection,
    channel: Channel,
    wintun_path: Path,
    load=load_wintun,
    make_adapter=VirtualAdapter,
    assign=assign_ipv4,
) -> int:
    """Create the adapter, serve the app, and remove the adapter again."""
    adapter = None
    try:
        wintun = load(wintun_path)
        forward_log(wintun, logging.getLogger("wintun"))
        adapter = make_adapter(wintun)
        adapter.open()
        log.info("%s the %s adapter", "Created" if adapter.created else "Opened", adapter.name)
        adapter.start()
        return Helper(connection, channel, adapter, assign).run()
    except (WintunLoadError, AdapterError, HelperError, ValueError) as exc:
        channel.send(protocol.ERROR + str(exc).encode())
        return 1
    finally:
        if adapter is not None:
            created = adapter.created
            adapter.close()
            log.info("Removed the adapter" if created else "Released the adapter")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"steamlan {protocol.HELPER_FLAG}")
    parser.add_argument("--pipe", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--wintun", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.pipe.startswith(_LOCAL_PIPE):
        return 2
    try:
        connection = Client(args.pipe, family="AF_PIPE", authkey=bytes.fromhex(args.key))
    except (OSError, EOFError, ValueError, AuthenticationError):
        # Nothing has been changed yet.
        return 2

    channel = Channel(connection)
    root = logging.getLogger()
    root.addHandler(PipeLogHandler(channel))
    root.setLevel(logging.INFO)
    try:
        return serve(connection, channel, args.wintun)
    finally:
        channel.close()
        connection.close()
