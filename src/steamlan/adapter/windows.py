"""Windows side of the virtual adapter: Administrator rights and its IPv4 address."""

import ctypes
import ipaddress
import os
import subprocess
import sys
from ctypes import wintypes

from steamlan.adapter.adapter import ADMIN_HINT, AdapterError

_AF_INET = 2
_IP_DAD_STATE_PREFERRED = 4  # IpDadStatePreferred in nldef.h
_ERROR_ACCESS_DENIED = 5
_ERROR_OBJECT_ALREADY_EXISTS = 5010
_SW_SHOWNORMAL = 1


class MibUnicastIpAddressRow(ctypes.Structure):
    """MIB_UNICASTIPADDRESS_ROW from netioapi.h (default packing, 80 bytes)."""

    _fields_ = [
        # SOCKADDR_INET: a 28-byte union; as a sockaddr_in the family is at
        # offset 0 and the IPv4 address at offset 4.
        ("Address", ctypes.c_ubyte * 28),
        ("InterfaceLuid", ctypes.c_uint64),
        ("InterfaceIndex", wintypes.ULONG),
        ("PrefixOrigin", ctypes.c_int),
        ("SuffixOrigin", ctypes.c_int),
        ("ValidLifetime", wintypes.ULONG),
        ("PreferredLifetime", wintypes.ULONG),
        ("OnLinkPrefixLength", ctypes.c_uint8),
        ("SkipAsSource", ctypes.c_uint8),
        ("DadState", ctypes.c_int),
        ("ScopeId", wintypes.ULONG),
        ("CreationTimeStamp", ctypes.c_int64),
    ]


def is_admin() -> bool:
    shell32 = ctypes.WinDLL("shell32")
    shell32.IsUserAnAdmin.argtypes = []
    shell32.IsUserAnAdmin.restype = wintypes.BOOL
    return bool(shell32.IsUserAnAdmin())


def relaunch_as_admin(arguments: list[str]) -> bool:
    """Run this Python with arguments again, elevated, after Windows' UAC prompt.

    Returns False if it could not be started, e.g. when the prompt was declined.
    """
    shell32 = ctypes.WinDLL("shell32")
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    result = shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        subprocess.list2cmdline(arguments),
        os.getcwd(),
        _SW_SHOWNORMAL,
    )
    # ShellExecute reports success with a value greater than 32.
    return (result or 0) > 32


def _iphlpapi() -> ctypes.WinDLL:
    iphlpapi = ctypes.WinDLL("iphlpapi")
    row = ctypes.POINTER(MibUnicastIpAddressRow)
    iphlpapi.InitializeUnicastIpAddressEntry.argtypes = [row]
    iphlpapi.InitializeUnicastIpAddressEntry.restype = None
    iphlpapi.CreateUnicastIpAddressEntry.argtypes = [row]
    iphlpapi.CreateUnicastIpAddressEntry.restype = wintypes.DWORD
    return iphlpapi


def ipv4_address_row(
    luid: int, interface: ipaddress.IPv4Interface, iphlpapi=None
) -> MibUnicastIpAddressRow:
    row = MibUnicastIpAddressRow()
    (iphlpapi or _iphlpapi()).InitializeUnicastIpAddressEntry(ctypes.byref(row))
    row.Address[0:2] = _AF_INET.to_bytes(2, "little")
    row.Address[4:8] = interface.ip.packed
    row.InterfaceLuid = luid
    row.OnLinkPrefixLength = interface.network.prefixlen
    row.DadState = _IP_DAD_STATE_PREFERRED
    return row


def assign_ipv4(luid: int, interface: str, iphlpapi=None) -> bool:
    """Give the adapter with this LUID an address like "10.77.0.1/24".

    Like Wintun's own example this uses CreateUnicastIpAddressEntry: no gateway
    or DNS is set, so only the adapter's own subnet is routed through it, and
    no other adapter is touched. Returns False if it already had the address.
    """
    iphlpapi = iphlpapi or _iphlpapi()
    row = ipv4_address_row(luid, ipaddress.IPv4Interface(interface), iphlpapi)
    status = iphlpapi.CreateUnicastIpAddressEntry(ctypes.byref(row))
    if status == _ERROR_OBJECT_ALREADY_EXISTS:
        return False
    if status:
        message = f"CreateUnicastIpAddressEntry failed: {ctypes.FormatError(status).strip()}"
        if status == _ERROR_ACCESS_DENIED:
            message += f". {ADMIN_HINT}"
        raise AdapterError(f"{message} (error {status})", status)
    return True
