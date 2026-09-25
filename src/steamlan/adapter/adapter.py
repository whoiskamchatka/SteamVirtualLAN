"""A virtual network adapter that moves raw IPv4/IPv6 packets as bytes."""

import ctypes
import uuid
from ctypes import wintypes

from steamlan.adapter.wintun import (
    ERROR_ACCESS_DENIED,
    ERROR_NO_MORE_ITEMS,
    MAX_IP_PACKET_SIZE,
    MAX_RING_CAPACITY,
    MIN_RING_CAPACITY,
)

ADAPTER_NAME = "SteamVirtualLAN"
TUNNEL_TYPE = "SteamVirtualLAN"
# A fixed GUID makes Windows treat every SteamVirtualLAN adapter as the same
# network instead of adding a new network profile each time one is created.
ADAPTER_GUID = uuid.UUID("49afce7d-0317-47ee-8de5-2a1c5e4dde1d")
DEFAULT_RING_CAPACITY = 0x400000  # 4 MiB
ADMIN_HINT = "SteamVirtualLAN needs Administrator rights for its network adapter."

_WAIT_OBJECT_0 = 0


class AdapterError(Exception):
    def __init__(self, message: str, winerror: int = 0):
        super().__init__(message)
        self.winerror = winerror


def _failed(call: str, code: int) -> AdapterError:
    reason = ctypes.FormatError(code).strip() if code else "no error code"
    message = f"{call} failed: {reason} (error {code})"
    if code == ERROR_ACCESS_DENIED:
        message += f". {ADMIN_HINT}"
    return AdapterError(message, code)


def _wait_for_event(event: int, timeout_ms: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return kernel32.WaitForSingleObject(event, timeout_ms) == _WAIT_OBJECT_0


class VirtualAdapter:
    """A Wintun adapter and its packet session.

    An adapter this object created is removed again by close(); one that
    already existed is only released.
    """

    def __init__(self, wintun: ctypes.WinDLL, name: str = ADAPTER_NAME, wait=_wait_for_event):
        self._wintun = wintun
        self._wait = wait
        self.name = name
        self.created = False
        self._adapter: int | None = None
        self._session: int | None = None

    def open(self) -> None:
        """Open the adapter by name, or create it (needs Administrator)."""
        if self._adapter is not None:
            return
        adapter = self._wintun.WintunOpenAdapter(self.name)
        if not adapter:
            # const GUID *: Data1-3 are little-endian, which is uuid's bytes_le.
            guid = (ctypes.c_ubyte * 16).from_buffer_copy(ADAPTER_GUID.bytes_le)
            adapter = self._wintun.WintunCreateAdapter(self.name, TUNNEL_TYPE, guid)
            if not adapter:
                raise _failed("WintunCreateAdapter", ctypes.get_last_error())
            self.created = True
        self._adapter = adapter

    @property
    def luid(self) -> int:
        luid = ctypes.c_uint64()
        self._wintun.WintunGetAdapterLUID(self._opened(), ctypes.byref(luid))
        return luid.value

    def start(self, capacity: int = DEFAULT_RING_CAPACITY) -> None:
        if self._session is not None:
            return
        if not MIN_RING_CAPACITY <= capacity <= MAX_RING_CAPACITY or capacity & (capacity - 1):
            raise ValueError("ring capacity must be a power of two between 128 KiB and 64 MiB")
        session = self._wintun.WintunStartSession(self._opened(), capacity)
        if not session:
            raise _failed("WintunStartSession", ctypes.get_last_error())
        self._session = session

    def wait(self, timeout_ms: int) -> bool:
        """Wait until a packet may be ready to read; False on timeout."""
        # The event belongs to the session and must not be closed by us.
        event = self._wintun.WintunGetReadWaitEvent(self._started())
        return self._wait(event, timeout_ms)

    def read(self) -> bytes | None:
        """The next packet Windows sent into the adapter, or None if there is none."""
        session = self._started()
        size = wintypes.DWORD()
        packet = self._wintun.WintunReceivePacket(session, ctypes.byref(size))
        if not packet:
            code = ctypes.get_last_error()
            if code == ERROR_NO_MORE_ITEMS:
                return None
            raise _failed("WintunReceivePacket", code)
        # The packet lives in Wintun's ring until released; copy it out first.
        try:
            return ctypes.string_at(packet, size.value)
        finally:
            self._wintun.WintunReleaseReceivePacket(session, packet)

    def write(self, packet: bytes) -> None:
        """Hand one IPv4 or IPv6 packet to Windows as if it arrived on the adapter."""
        data = bytes(packet)
        if not data or len(data) > MAX_IP_PACKET_SIZE:
            raise ValueError(f"packet must be 1 to {MAX_IP_PACKET_SIZE} bytes")
        if data[0] >> 4 not in (4, 6):
            raise ValueError("packet is neither IPv4 nor IPv6")
        session = self._started()
        buffer = self._wintun.WintunAllocateSendPacket(session, len(data))
        if not buffer:
            raise _failed("WintunAllocateSendPacket", ctypes.get_last_error())
        # WintunSendPacket both sends and releases the allocated buffer.
        ctypes.memmove(buffer, data, len(data))
        self._wintun.WintunSendPacket(session, buffer)

    def close(self) -> None:
        session, self._session = self._session, None
        adapter, self._adapter = self._adapter, None
        if session:
            self._wintun.WintunEndSession(session)
        if adapter:
            self._wintun.WintunCloseAdapter(adapter)

    def _opened(self) -> int:
        if self._adapter is None:
            raise AdapterError("the adapter is not open")
        return self._adapter

    def _started(self) -> int:
        if self._session is None:
            raise AdapterError("the adapter session is not started")
        return self._session

    def __enter__(self) -> "VirtualAdapter":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
