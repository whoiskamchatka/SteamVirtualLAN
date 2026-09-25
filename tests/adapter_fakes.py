"""Fakes for the network helper tests: a Wintun adapter and one end of the pipe."""

import queue
import threading
import time
from ipaddress import IPv4Address

from steamlan.adapter import protocol
from steamlan.adapter.adapter import AdapterError


def ipv4_packet(source, destination, payload=b"ping"):
    header = bytearray(20)
    header[0] = 0x45
    header[2:4] = (20 + len(payload)).to_bytes(2, "big")
    header[8] = 128
    header[9] = 1
    header[12:16] = IPv4Address(source).packed
    header[16:20] = IPv4Address(destination).packed
    return bytes(header) + payload


class FakeAdapter:
    """A started VirtualAdapter: packets "from Windows" are queued with arrive()."""

    name = "SteamVirtualLAN"
    luid = 0x1234

    def __init__(self, created=True, fail_open=None):
        self.created = created
        self.fail_open = fail_open
        self.events = []
        self.written = []
        self.write_error = None
        self.read_error = None
        self._incoming = []
        self._lock = threading.Lock()
        self._arrived = threading.Event()

    def open(self):
        self.events.append("open")
        if self.fail_open:
            raise self.fail_open

    def start(self):
        self.events.append("start")

    def close(self):
        self.events.append("close")

    def arrive(self, *packets):
        with self._lock:
            self._incoming.extend(packets)
        self._arrived.set()

    def pending(self):
        with self._lock:
            return len(self._incoming)

    def wait(self, timeout_ms):
        return self._arrived.wait(timeout_ms / 1000)

    def read(self):
        if self.read_error:
            raise self.read_error
        with self._lock:
            if self._incoming:
                return self._incoming.pop(0)
            self._arrived.clear()
            return None

    def write(self, packet):
        if self.write_error:
            raise self.write_error
        self.written.append(packet)


def buffer_full():
    return AdapterError("WintunAllocateSendPacket failed", 111)


class FakeConnection:
    """The helper's end of the pipe. recv_bytes() waits for messages the test
    gives it; finish() makes it report that the app closed the pipe."""

    _EOF = object()

    def __init__(self, *incoming, finished=True):
        self.incoming = queue.Queue()
        for message in incoming:
            self.incoming.put(message)
        if finished:
            self.finish()
        self.sent = []
        self.closed = False

    def give(self, *messages):
        for message in messages:
            self.incoming.put(message)

    def finish(self):
        self.incoming.put(self._EOF)

    def recv_bytes(self, maxlength=None):
        message = self.incoming.get(timeout=5)
        if message is self._EOF:
            raise EOFError
        return message

    def send_bytes(self, message):
        self.sent.append(bytes(message))

    def close(self):
        self.closed = True

    def tags(self):
        return [message[:1] for message in self.sent]

    def of(self, tag):
        return [message[1:] for message in self.sent if message[:1] == tag]


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.01)


def set_address(address):
    return protocol.SET_ADDRESS + IPv4Address(address).packed


def packet(data):
    return protocol.PACKET + data
