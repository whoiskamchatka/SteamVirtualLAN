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
_ERROR_CANCELLED = 1223  # the user declined the UAC prompt
_SW_HIDE = 0
_SW_SHOWNORMAL = 1
_SEE_MASK_NOCLOSEPROCESS = 0x40
_SEE_MASK_NOASYNC = 0x100
_SEE_MASK_FLAG_NO_UI = 0x400
_WAIT_OBJECT_0 = 0


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


class ShellExecuteInfo(ctypes.Structure):
    """SHELLEXECUTEINFOW from shellapi.h (default packing, 112 bytes on x64)."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _kernel32() -> ctypes.WinDLL:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessId.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class Process:
    """A process handle: enough to see whether and how a started process ended."""

    def __init__(self, handle: int, kernel32=None):
        self._handle = handle
        self._kernel32 = kernel32 or _kernel32()
        self.pid = self._kernel32.GetProcessId(handle)

    def wait(self, timeout: float) -> int | None:
        """Its exit code once it has exited, waiting up to timeout seconds; else None."""
        if self._handle is None:
            raise ValueError("the process handle is closed")
        milliseconds = max(0, int(timeout * 1000))
        if self._kernel32.WaitForSingleObject(self._handle, milliseconds) != _WAIT_OBJECT_0:
            return None
        code = wintypes.DWORD()
        if not self._kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            return None
        return code.value

    def poll(self) -> int | None:
        return self.wait(0)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            self._kernel32.CloseHandle(handle)


def start_process(
    executable: str, arguments: list[str], elevate: bool, show: int = _SW_HIDE, shell32=None
) -> Process | None:
    """Start a program, through Windows' UAC prompt if elevate, and keep its handle.

    Returns None when the user declined the UAC prompt; raises AdapterError
    when the program could not be started for another reason.
    """
    if shell32 is None:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
        shell32.ShellExecuteExW.restype = wintypes.BOOL
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(ShellExecuteInfo)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC | _SEE_MASK_FLAG_NO_UI
    info.lpVerb = "runas" if elevate else "open"
    info.lpFile = executable
    info.lpParameters = subprocess.list2cmdline(arguments)
    info.lpDirectory = os.getcwd()
    info.nShow = show
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == _ERROR_CANCELLED:
            return None
        reason = ctypes.FormatError(code).strip() if code else "no error code"
        raise AdapterError(f"could not start {executable}: {reason} (error {code})", code)
    if not info.hProcess:
        raise AdapterError(f"Windows started {executable} without a process handle")
    return Process(info.hProcess)


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
