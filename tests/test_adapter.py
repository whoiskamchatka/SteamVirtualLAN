import ctypes
import hashlib
import ipaddress
from ctypes import wintypes
from unittest import mock

import pytest

from steamlan.adapter import AdapterError, VirtualAdapter, WintunLoadError, ipv4, load_wintun
from steamlan.adapter import wintun as wintun_module
from steamlan.adapter.adapter import ADAPTER_GUID
from steamlan.adapter.windows import MibUnicastIpAddressRow, assign_ipv4, ipv4_address_row
from steamlan.adapter.wintun import (
    ERROR_ACCESS_DENIED,
    ERROR_BUFFER_OVERFLOW,
    ERROR_HANDLE_EOF,
    ERROR_NO_MORE_ITEMS,
    MAX_IP_PACKET_SIZE,
    LoggerCallback,
    bind,
    driver_version,
    find_wintun,
    wintun_arch,
)

ADAPTER = 0x1000
SESSION = 0x2000
EVENT = 0x3000


def ipv4_packet(source="10.77.0.1", destination="10.77.0.2", protocol=1, payload=b"x" * 8):
    header = bytearray(20)
    header[0] = 0x45
    header[2:4] = (20 + len(payload)).to_bytes(2, "big")
    header[8] = 128
    header[9] = protocol
    header[12:16] = ipaddress.IPv4Address(source).packed
    header[16:20] = ipaddress.IPv4Address(destination).packed
    return bytes(header) + payload


def failing(code, value=None):
    """A native function that returns value and leaves code in GetLastError."""

    def call(*args):
        ctypes.set_last_error(code)
        return value

    return call


class FakeWintun:
    """Stands in for wintun.dll with real buffers for packets."""

    def __init__(self, existing=False):
        self.existing = existing
        self.calls = []
        self.inbox = []
        self.received = {}
        self.released = []
        self.sent = []
        self.WintunOpenAdapter = mock.Mock(side_effect=self._open)
        self.WintunCreateAdapter = mock.Mock(return_value=ADAPTER)
        self.WintunCloseAdapter = mock.Mock()
        self.WintunGetAdapterLUID = mock.Mock(side_effect=self._luid)
        self.WintunStartSession = mock.Mock(return_value=SESSION)
        self.WintunEndSession = mock.Mock()
        self.WintunGetReadWaitEvent = mock.Mock(return_value=EVENT)
        self.WintunReceivePacket = mock.Mock(side_effect=self._receive)
        self.WintunReleaseReceivePacket = mock.Mock(side_effect=self._release)
        self.WintunAllocateSendPacket = mock.Mock(side_effect=self._allocate)
        self.WintunSendPacket = mock.Mock(side_effect=self._send)
        self.buffers = {}

    def _open(self, name):
        if self.existing:
            return ADAPTER
        ctypes.set_last_error(2)
        return None

    def _luid(self, adapter, luid):
        ctypes.cast(luid, ctypes.POINTER(ctypes.c_uint64)).contents.value = 0x0006000001000000

    def _receive(self, session, size):
        if not self.inbox:
            ctypes.set_last_error(ERROR_NO_MORE_ITEMS)
            return None
        packet = self.inbox.pop(0)
        buffer = ctypes.create_string_buffer(packet, len(packet))
        address = ctypes.addressof(buffer)
        self.received[address] = buffer
        ctypes.cast(size, ctypes.POINTER(wintypes.DWORD)).contents.value = len(packet)
        return address

    def _release(self, session, packet):
        buffer = self.received.pop(packet)
        ctypes.memset(buffer, 0xDD, len(buffer))
        self.released.append(packet)

    def _allocate(self, session, size):
        buffer = ctypes.create_string_buffer(size)
        self.buffers[ctypes.addressof(buffer)] = buffer
        return ctypes.addressof(buffer)

    def _send(self, session, packet):
        self.sent.append(self.buffers.pop(packet).raw)


@pytest.fixture
def wintun():
    return FakeWintun()


@pytest.fixture
def adapter(wintun):
    adapter = VirtualAdapter(wintun, wait=lambda event, timeout: True)
    adapter.open()
    adapter.start()
    yield adapter
    adapter.close()


def test_wintun_signatures():
    lib = bind(mock.Mock())
    handle, dword = ctypes.c_void_p, wintypes.DWORD

    expected = {
        "WintunCreateAdapter": ([ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p], handle),
        "WintunOpenAdapter": ([ctypes.c_wchar_p], handle),
        "WintunCloseAdapter": ([handle], None),
        "WintunDeleteDriver": ([], wintypes.BOOL),
        "WintunGetAdapterLUID": ([handle, ctypes.POINTER(ctypes.c_uint64)], None),
        "WintunGetRunningDriverVersion": ([], dword),
        "WintunSetLogger": ([LoggerCallback], None),
        "WintunStartSession": ([handle, dword], handle),
        "WintunEndSession": ([handle], None),
        "WintunGetReadWaitEvent": ([handle], wintypes.HANDLE),
        "WintunReceivePacket": ([handle, ctypes.POINTER(dword)], ctypes.c_void_p),
        "WintunReleaseReceivePacket": ([handle, ctypes.c_void_p], None),
        "WintunAllocateSendPacket": ([handle, dword], ctypes.c_void_p),
        "WintunSendPacket": ([handle, ctypes.c_void_p], None),
    }
    for name, (argtypes, restype) in expected.items():
        assert getattr(lib, name).argtypes == argtypes, name
        assert getattr(lib, name).restype is restype, name


def test_wintun_types():
    assert ctypes.sizeof(wintypes.DWORD) == 4
    assert ctypes.sizeof(ctypes.c_uint64) == 8  # NET_LUID
    assert MAX_IP_PACKET_SIZE == 0xFFFF


def test_load_wintun_missing(tmp_path):
    with pytest.raises(WintunLoadError, match="wintun.dll not found"):
        load_wintun(tmp_path)


def test_load_wintun_unsupported_dll():
    with pytest.raises(WintunLoadError, match="unsupported wintun.dll"):
        bind(mock.Mock(spec=["WintunCreateAdapter"]))


def test_open_existing_adapter():
    wintun = FakeWintun(existing=True)
    adapter = VirtualAdapter(wintun)

    adapter.open()

    assert not adapter.created
    wintun.WintunCreateAdapter.assert_not_called()


def test_create_missing_adapter(wintun):
    adapter = VirtualAdapter(wintun)

    adapter.open()
    adapter.open()

    assert adapter.created
    name, tunnel_type, guid = wintun.WintunCreateAdapter.call_args.args
    assert (name, tunnel_type) == ("SteamVirtualLAN", "SteamVirtualLAN")
    assert bytes(guid) == ADAPTER_GUID.bytes_le


def test_create_without_administrator(wintun):
    wintun.WintunCreateAdapter.side_effect = failing(ERROR_ACCESS_DENIED)

    with pytest.raises(AdapterError, match="Administrator") as excinfo:
        VirtualAdapter(wintun).open()
    assert excinfo.value.winerror == ERROR_ACCESS_DENIED


def test_luid(adapter):
    assert adapter.luid == 0x0006000001000000


@pytest.mark.parametrize("capacity", [0x10000, 0x8000000, 0x30000])
def test_invalid_ring_capacity(wintun, capacity):
    adapter = VirtualAdapter(wintun)
    adapter.open()

    with pytest.raises(ValueError, match="power of two"):
        adapter.start(capacity)
    wintun.WintunStartSession.assert_not_called()


def test_start_session_failure(wintun):
    wintun.WintunStartSession.side_effect = failing(ERROR_ACCESS_DENIED)
    adapter = VirtualAdapter(wintun)
    adapter.open()

    with pytest.raises(AdapterError, match="WintunStartSession failed"):
        adapter.start()


def test_read_copies_and_releases_packet(adapter, wintun):
    packet = ipv4_packet()
    wintun.inbox.append(packet)

    assert adapter.read() == packet
    assert len(wintun.released) == 1
    assert wintun.received == {}


def test_read_without_packets(adapter, wintun):
    assert adapter.read() is None
    wintun.WintunReleaseReceivePacket.assert_not_called()


@pytest.mark.parametrize("code", [ERROR_HANDLE_EOF, 13])
def test_read_error(adapter, wintun, code):
    wintun.WintunReceivePacket.side_effect = failing(code)

    with pytest.raises(AdapterError, match="WintunReceivePacket failed") as excinfo:
        adapter.read()
    assert excinfo.value.winerror == code


def test_write(adapter, wintun):
    packet = ipv4_packet()

    adapter.write(packet)

    assert wintun.sent == [packet]
    wintun.WintunAllocateSendPacket.assert_called_once_with(SESSION, len(packet))


def test_write_ipv6(adapter, wintun):
    adapter.write(b"\x60" + b"\0" * 39)

    assert len(wintun.sent) == 1


@pytest.mark.parametrize(
    "packet",
    [b"", b"\x45" * (MAX_IP_PACKET_SIZE + 1), b"\x00" * 20, b"\x50" * 20],
    ids=["empty", "too-long", "version-0", "version-5"],
)
def test_write_rejects_invalid_packets(adapter, wintun, packet):
    with pytest.raises(ValueError):
        adapter.write(packet)
    wintun.WintunAllocateSendPacket.assert_not_called()


def test_write_when_ring_is_full(adapter, wintun):
    wintun.WintunAllocateSendPacket.side_effect = failing(ERROR_BUFFER_OVERFLOW)

    with pytest.raises(AdapterError, match="WintunAllocateSendPacket failed"):
        adapter.write(ipv4_packet())
    wintun.WintunSendPacket.assert_not_called()


def test_wait_uses_session_event(wintun):
    waits = []
    adapter = VirtualAdapter(wintun, wait=lambda event, timeout: waits.append((event, timeout)))
    adapter.open()
    adapter.start()

    adapter.wait(250)

    assert waits == [(EVENT, 250)]


def test_close_ends_session_then_closes_adapter(wintun):
    order = []
    wintun.WintunEndSession.side_effect = lambda session: order.append(("end", session))
    wintun.WintunCloseAdapter.side_effect = lambda adapter: order.append(("close", adapter))
    adapter = VirtualAdapter(wintun)
    adapter.open()
    adapter.start()

    adapter.close()
    adapter.close()

    assert order == [("end", SESSION), ("close", ADAPTER)]


def test_close_before_open_does_nothing(wintun):
    VirtualAdapter(wintun).close()

    wintun.WintunCloseAdapter.assert_not_called()


@pytest.mark.parametrize(
    "use", [lambda a: a.read(), lambda a: a.write(ipv4_packet()), lambda a: a.wait(1)]
)
def test_packets_need_a_started_session(wintun, use):
    adapter = VirtualAdapter(wintun)
    adapter.open()

    with pytest.raises(AdapterError, match="not started"):
        use(adapter)

    adapter.start()
    adapter.close()
    with pytest.raises(AdapterError, match="not started"):
        use(adapter)


def test_context_manager_closes(wintun):
    with VirtualAdapter(wintun) as adapter:
        adapter.start()

    wintun.WintunEndSession.assert_called_once_with(SESSION)
    wintun.WintunCloseAdapter.assert_called_once_with(ADAPTER)


def test_parse_ipv4_header():
    header = ipv4.parse_ipv4_header(ipv4_packet(protocol=17))

    assert header == ipv4.IPv4Header("10.77.0.1", "10.77.0.2", 17, 20, 28)
    assert header.protocol_name == "UDP"


@pytest.mark.parametrize(
    ("protocol", "name"), [(1, "ICMP"), (2, "IGMP"), (6, "TCP"), (17, "UDP"), (99, "protocol 99")]
)
def test_protocol_names(protocol, name):
    assert ipv4.parse_ipv4_header(ipv4_packet(protocol=protocol)).protocol_name == name


def test_ipv4_header_with_options():
    packet = bytearray(ipv4_packet(payload=b"\x01" * 4 + b"x" * 8))
    packet[0] = 0x46
    header = ipv4.parse_ipv4_header(bytes(packet))

    assert header.header_length == 24


@pytest.mark.parametrize(
    ("packet", "error"),
    [
        (ipv4_packet()[:19], "too short"),
        (b"\x60" + b"\0" * 39, "not an IPv4"),
        (b"\x44" + ipv4_packet()[1:], "header length 16"),
        (b"\x4f" + ipv4_packet()[1:], "header length 60"),
        (ipv4_packet()[:2] + (100).to_bytes(2, "big") + ipv4_packet()[4:], "total length 100"),
        (ipv4_packet()[:2] + (10).to_bytes(2, "big") + ipv4_packet()[4:], "total length 10"),
    ],
)
def test_malformed_ipv4(packet, error):
    with pytest.raises(ValueError, match=error):
        ipv4.parse_ipv4_header(packet)


@pytest.mark.parametrize(
    ("packet", "text"),
    [
        (ipv4_packet(), "IPv4 ICMP 10.77.0.1 -> 10.77.0.2, 28 bytes"),
        (b"\x60" + b"\0" * 39, "IPv6 packet, 40 bytes"),
        (b"", "Unknown packet, 0 bytes"),
        (b"\x10\x00", "Unknown packet, 2 bytes"),
        (b"\x45\x00", "Malformed IPv4 packet, 2 bytes"),
    ],
)
def test_describe_packet(packet, text):
    assert ipv4.describe_packet(packet).startswith(text)


def test_fixed_adapter_guid_layout():
    # GUID {49afce7d-0317-47ee-8de5-2a1c5e4dde1d} as Windows lays it out in memory.
    assert ADAPTER_GUID.bytes_le == bytes.fromhex("7dceaf491703ee478de52a1c5e4dde1d")


@pytest.mark.parametrize(
    ("machine", "is_64bit", "arch"),
    [
        ("AMD64", True, "amd64"),
        ("x86", False, "x86"),
        ("AMD64", False, "x86"),
        ("ARM64", True, "arm64"),
        ("ARM64", False, "arm"),
        ("ARM", False, "arm"),
    ],
)
def test_wintun_arch(machine, is_64bit, arch):
    assert wintun_arch(machine, is_64bit) == arch


def test_wintun_search_order(tmp_path, monkeypatch):
    monkeypatch.delenv("STEAMLAN_WINTUN", raising=False)
    monkeypatch.setattr(wintun_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(wintun_module, "wintun_arch", lambda: "amd64")

    assert wintun_module.candidate_paths() == [
        tmp_path / "wintun" / "bin" / "amd64" / "wintun.dll",
    ]


def test_wintun_next_to_packaged_exe(tmp_path, monkeypatch):
    monkeypatch.delenv("STEAMLAN_WINTUN", raising=False)
    monkeypatch.setattr(wintun_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(wintun_module.sys, "executable", str(tmp_path / "SteamVirtualLAN.exe"))

    assert wintun_module.candidate_paths()[0] == tmp_path / "wintun.dll"


@pytest.mark.parametrize("as_directory", [True, False])
def test_wintun_path_from_environment(tmp_path, monkeypatch, as_directory):
    dll = tmp_path / "wintun.dll"
    monkeypatch.setenv("STEAMLAN_WINTUN", str(tmp_path if as_directory else dll))

    assert wintun_module.candidate_paths()[0] == dll


def test_find_wintun_explains_what_to_download(tmp_path, monkeypatch):
    monkeypatch.delenv("STEAMLAN_WINTUN", raising=False)
    monkeypatch.setattr(wintun_module, "_PROJECT_ROOT", tmp_path)

    with pytest.raises(WintunLoadError, match="not found. Looked in"):
        find_wintun()


def test_load_wintun_wrong_architecture(tmp_path, monkeypatch):
    dll = tmp_path / "wintun.dll"
    dll.write_bytes(b"")
    error = OSError("[WinError 193] %1 is not a valid Win32 application")
    error.winerror = 193
    monkeypatch.setattr(wintun_module, "is_official_dll", lambda path: True)
    monkeypatch.setattr(wintun_module.ctypes, "WinDLL", mock.Mock(side_effect=error))

    with pytest.raises(WintunLoadError, match="not the .* build"):
        load_wintun(dll)


def test_load_wintun_uses_restricted_search(tmp_path, monkeypatch):
    dll = tmp_path / "wintun.dll"
    dll.write_bytes(b"")
    windll = mock.Mock(return_value=mock.Mock())
    monkeypatch.setattr(wintun_module, "is_official_dll", lambda path: True)
    monkeypatch.setattr(wintun_module.ctypes, "WinDLL", windll)

    lib = load_wintun(dll)

    assert windll.call_args.kwargs == {"winmode": 0x100 | 0x800, "use_last_error": True}
    assert lib.path == dll


def test_driver_version():
    lib = mock.Mock()
    lib.WintunGetRunningDriverVersion.return_value = (0 << 16) | 14
    assert driver_version(lib) == "0.14"

    lib.WintunGetRunningDriverVersion.return_value = 0
    assert driver_version(lib) is None


def test_unicast_address_row_layout():
    assert ctypes.sizeof(MibUnicastIpAddressRow) == 80
    assert ctypes.alignment(MibUnicastIpAddressRow) == 8
    offsets = {
        name: getattr(MibUnicastIpAddressRow, name).offset
        for name, _ in MibUnicastIpAddressRow._fields_
    }
    assert offsets == {
        "Address": 0,
        "InterfaceLuid": 32,
        "InterfaceIndex": 40,
        "PrefixOrigin": 44,
        "SuffixOrigin": 48,
        "ValidLifetime": 52,
        "PreferredLifetime": 56,
        "OnLinkPrefixLength": 60,
        "SkipAsSource": 61,
        "DadState": 64,
        "ScopeId": 68,
        "CreationTimeStamp": 72,
    }


class FakeIpHelper:
    def __init__(self, status=0):
        self.status = status
        self.created = []

    def InitializeUnicastIpAddressEntry(self, row):
        row._obj.ValidLifetime = 0xFFFFFFFF
        row._obj.PreferredLifetime = 0xFFFFFFFF

    def CreateUnicastIpAddressEntry(self, row):
        self.created.append(bytes(row._obj))
        return self.status


def test_ipv4_address_row():
    row = ipv4_address_row(0x1234, ipaddress.IPv4Interface("10.77.0.1/24"), FakeIpHelper())

    assert bytes(row.Address[0:2]) == b"\x02\x00"  # AF_INET
    assert bytes(row.Address[4:8]) == bytes([10, 77, 0, 1])
    assert row.InterfaceLuid == 0x1234
    assert row.OnLinkPrefixLength == 24
    assert row.DadState == 4  # IpDadStatePreferred
    assert row.ValidLifetime == 0xFFFFFFFF


def test_assign_ipv4():
    helper = FakeIpHelper()

    assert assign_ipv4(0x1234, "10.77.0.1/24", helper) is True
    assert len(helper.created) == 1


def test_assign_ipv4_already_present():
    assert assign_ipv4(0x1234, "10.77.0.1/24", FakeIpHelper(status=5010)) is False


@pytest.mark.parametrize(("status", "message"), [(5, "Administrator"), (87, "error 87")])
def test_assign_ipv4_failure(status, message):
    with pytest.raises(AdapterError, match=message) as excinfo:
        assign_ipv4(0x1234, "10.77.0.1/24", FakeIpHelper(status=status))
    assert excinfo.value.winerror == status


def test_assign_ipv4_rejects_bad_address():
    with pytest.raises(ValueError):
        assign_ipv4(0x1234, "10.77.0.300/24", FakeIpHelper())


def test_load_wintun_refuses_unofficial_dll(tmp_path, monkeypatch):
    dll = tmp_path / "wintun.dll"
    dll.write_bytes(b"MZ not the official build")
    windll = mock.Mock()
    monkeypatch.setattr(wintun_module.ctypes, "WinDLL", windll)

    with pytest.raises(WintunLoadError, match="not the official Wintun 0.14.1"):
        load_wintun(dll)
    windll.assert_not_called()


def test_is_official_dll(tmp_path, monkeypatch):
    dll = tmp_path / "wintun.dll"
    dll.write_bytes(b"dll")
    monkeypatch.setitem(wintun_module.DLL_SHA256, "amd64", "0" * 64)
    assert not wintun_module.is_official_dll(dll, "amd64")

    monkeypatch.setitem(wintun_module.DLL_SHA256, "amd64", hashlib.sha256(b"dll").hexdigest())
    assert wintun_module.is_official_dll(dll, "amd64")
    assert not wintun_module.is_official_dll(tmp_path / "missing.dll", "amd64")


def test_official_hashes_are_pinned():
    assert wintun_module.PACKAGE_URL == "https://www.wintun.net/builds/wintun-0.14.1.zip"
    assert wintun_module.PACKAGE_SHA256 == (
        "07c256185d6ee3652e09fa55c0b673e2624b565e02c4b9091c79ca7d2f24ef51"
    )
    assert set(wintun_module.DLL_SHA256) == {"amd64", "arm", "arm64", "x86"}
    assert all(len(value) == 64 for value in wintun_module.DLL_SHA256.values())
