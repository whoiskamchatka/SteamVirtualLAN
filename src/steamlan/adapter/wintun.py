"""ctypes binding for wintun.dll, following wintun.h from Wintun 0.14.1.

Wintun is distributed as signed wintun.dll files from https://www.wintun.net,
one per CPU architecture, meant to be shipped side by side with the program
that uses them. The DLL installs its driver itself the first time an adapter
is created. It is not part of this repository.

All Wintun functions are WINAPI and report errors through GetLastError.
"""

import ctypes
import hashlib
import logging
import os
import platform
import sys
from ctypes import wintypes
from pathlib import Path

WINTUN_DLL = "wintun.dll"
WINTUN_VERSION = "0.14.1"
PACKAGE_URL = f"https://www.wintun.net/builds/wintun-{WINTUN_VERSION}.zip"
# Published on https://www.wintun.net next to the download link.
PACKAGE_SHA256 = "07c256185d6ee3652e09fa55c0b673e2624b565e02c4b9091c79ca7d2f24ef51"
# The wintun.dll files inside that package, taken from a verified download, so
# a cached or bundled copy can be checked before it is ever loaded.
DLL_SHA256 = {
    "amd64": "e5da8447dc2c320edc0fc52fa01885c103de8c118481f683643cacc3220dafce",
    "arm": "daad267411ecdc70a0535e274d2c3e9da3d0084bdac7662cb8424dd4a031b4d9",
    "arm64": "f7ba89005544be9d85231a9e0d5f23b2d15b3311667e2dad0debd344918a3f80",
    "x86": "d694fa46ab4cfebcb2632d094c7aa97278eef2f8052438621766d863ae98a931",
}

MIN_RING_CAPACITY = 0x20000  # WINTUN_MIN_RING_CAPACITY, 128 KiB
MAX_RING_CAPACITY = 0x4000000  # WINTUN_MAX_RING_CAPACITY, 64 MiB
MAX_IP_PACKET_SIZE = 0xFFFF  # WINTUN_MAX_IP_PACKET_SIZE

# Win32 error codes the Wintun functions document.
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_DATA = 13
ERROR_HANDLE_EOF = 38
ERROR_BUFFER_OVERFLOW = 111
ERROR_BAD_EXE_FORMAT = 193
ERROR_NO_MORE_ITEMS = 259

# Wintun's example loads the DLL with LOAD_LIBRARY_SEARCH_APPLICATION_DIR |
# LOAD_LIBRARY_SEARCH_SYSTEM32. We load by full path, so the equivalent is the
# DLL's own directory plus System32.
_LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x100
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800

# typedef VOID(CALLBACK *WINTUN_LOGGER_CALLBACK)(WINTUN_LOGGER_LEVEL, DWORD64, LPCWSTR)
LoggerCallback = ctypes.WINFUNCTYPE(None, ctypes.c_int, ctypes.c_uint64, ctypes.c_wchar_p)
_LOG_LEVELS = {0: logging.INFO, 1: logging.WARNING, 2: logging.ERROR}

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class WintunLoadError(Exception):
    pass


def wintun_arch(machine: str | None = None, is_64bit: bool | None = None) -> str:
    """The folder name of the Wintun build this Python process can load."""
    machine = (machine or platform.machine()).lower()
    is_64bit = sys.maxsize > 2**32 if is_64bit is None else is_64bit
    if machine in ("arm64", "aarch64"):
        return "arm64" if is_64bit else "arm"
    if machine.startswith("arm"):
        return "arm"
    return "amd64" if is_64bit else "x86"


def cache_dir() -> Path:
    """Where a downloaded wintun.dll is kept: the ignored ./wintun of the repository,
    or the user's local app data for a packaged SteamVirtualLAN."""
    if getattr(sys, "frozen", False):
        return Path(os.environ["LOCALAPPDATA"]) / "SteamVirtualLAN" / "wintun"
    return _PROJECT_ROOT / "wintun"


def cached_dll(arch: str | None = None) -> Path:
    # The same layout as the official zip.
    return cache_dir() / "bin" / (arch or wintun_arch()) / WINTUN_DLL


def candidate_paths() -> list[Path]:
    """Where wintun.dll is looked for, in order."""
    paths = []
    if configured := os.environ.get("STEAMLAN_WINTUN"):
        configured_path = Path(configured)
        paths.append(configured_path / WINTUN_DLL if configured_path.is_dir() else configured_path)
    if getattr(sys, "frozen", False):
        # A packaged SteamVirtualLAN.exe ships wintun.dll next to itself.
        paths.append(Path(sys.executable).resolve().parent / WINTUN_DLL)
    paths.append(cached_dll())
    return paths


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_official_dll(path: Path, arch: str | None = None) -> bool:
    """Whether path holds exactly the official wintun.dll for this architecture."""
    try:
        return sha256_of(path) == DLL_SHA256.get(arch or wintun_arch())
    except OSError:
        return False


def find_wintun() -> Path:
    for path in candidate_paths():
        if path.is_file():
            return path
    searched = "\n  ".join(str(path) for path in candidate_paths())
    raise WintunLoadError(f"{WINTUN_DLL} ({wintun_arch()}) not found. Looked in:\n  {searched}")


def load_wintun(path: str | os.PathLike[str] | None = None) -> ctypes.WinDLL:
    """Load wintun.dll from path (the DLL or its directory), or find it.

    Only the official Wintun build for this architecture is loaded.
    """
    dll_path = Path(path) if path is not None else find_wintun()
    if dll_path.is_dir():
        dll_path = dll_path / WINTUN_DLL
    if not dll_path.is_file():
        raise WintunLoadError(f"{WINTUN_DLL} not found at {dll_path}")
    if not is_official_dll(dll_path):
        raise WintunLoadError(
            f"{dll_path} is not the official Wintun {WINTUN_VERSION} {wintun_arch()} build; "
            "refusing to load it"
        )
    try:
        lib = ctypes.WinDLL(
            str(dll_path.resolve()),
            winmode=_LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | _LOAD_LIBRARY_SEARCH_SYSTEM32,
            use_last_error=True,
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) == ERROR_BAD_EXE_FORMAT:
            raise WintunLoadError(
                f"{dll_path} is not the {wintun_arch()} build of Wintun this Python needs"
            ) from exc
        raise WintunLoadError(f"could not load {dll_path}: {exc}") from exc
    lib.path = dll_path
    return bind(lib)


def bind(lib: ctypes.WinDLL) -> ctypes.WinDLL:
    """Declare the signatures from wintun.h. Handles and packet pointers are c_void_p."""
    handle = ctypes.c_void_p
    try:
        # WINTUN_ADAPTER_HANDLE WintunCreateAdapter(LPCWSTR Name, LPCWSTR TunnelType,
        #                                          const GUID *RequestedGUID)
        lib.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p]
        lib.WintunCreateAdapter.restype = handle
        lib.WintunOpenAdapter.argtypes = [ctypes.c_wchar_p]
        lib.WintunOpenAdapter.restype = handle
        lib.WintunCloseAdapter.argtypes = [handle]
        lib.WintunCloseAdapter.restype = None
        lib.WintunDeleteDriver.argtypes = []
        lib.WintunDeleteDriver.restype = wintypes.BOOL
        # NET_LUID is a union around a ULONG64.
        lib.WintunGetAdapterLUID.argtypes = [handle, ctypes.POINTER(ctypes.c_uint64)]
        lib.WintunGetAdapterLUID.restype = None
        lib.WintunGetRunningDriverVersion.argtypes = []
        lib.WintunGetRunningDriverVersion.restype = wintypes.DWORD
        lib.WintunSetLogger.argtypes = [LoggerCallback]
        lib.WintunSetLogger.restype = None

        lib.WintunStartSession.argtypes = [handle, wintypes.DWORD]
        lib.WintunStartSession.restype = handle
        lib.WintunEndSession.argtypes = [handle]
        lib.WintunEndSession.restype = None
        lib.WintunGetReadWaitEvent.argtypes = [handle]
        lib.WintunGetReadWaitEvent.restype = wintypes.HANDLE

        # BYTE *WintunReceivePacket(WINTUN_SESSION_HANDLE Session, DWORD *PacketSize)
        lib.WintunReceivePacket.argtypes = [handle, ctypes.POINTER(wintypes.DWORD)]
        lib.WintunReceivePacket.restype = ctypes.c_void_p
        lib.WintunReleaseReceivePacket.argtypes = [handle, ctypes.c_void_p]
        lib.WintunReleaseReceivePacket.restype = None
        lib.WintunAllocateSendPacket.argtypes = [handle, wintypes.DWORD]
        lib.WintunAllocateSendPacket.restype = ctypes.c_void_p
        lib.WintunSendPacket.argtypes = [handle, ctypes.c_void_p]
        lib.WintunSendPacket.restype = None
    except AttributeError as exc:
        raise WintunLoadError(f"unsupported {WINTUN_DLL}: {exc}") from exc
    return lib


def driver_version(lib: ctypes.WinDLL) -> str | None:
    """Version of the loaded Wintun driver, or None while it is not loaded."""
    version = lib.WintunGetRunningDriverVersion()
    return f"{(version >> 16) & 0xFF}.{version & 0xFF}" if version else None


def forward_log(lib: ctypes.WinDLL, logger: logging.Logger) -> None:
    """Send Wintun's own diagnostic messages to a Python logger."""

    def log_message(level: int, timestamp: int, message: str) -> None:
        logger.log(_LOG_LEVELS.get(level, logging.INFO), "%s", message)

    # Wintun keeps calling this pointer, possibly from other threads, so it has
    # to stay alive as long as the library.
    lib._steamlan_logger = LoggerCallback(log_message)
    lib.WintunSetLogger(lib._steamlan_logger)
