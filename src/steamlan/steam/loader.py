import ctypes
import os
from pathlib import Path

STEAM_API_DLL = "steam_api64.dll"

# Windows error returned by LoadLibrary when the file isn't a valid DLL for this
# process: either not a PE image at all, or a 64-bit DLL in a 32-bit Python.
_ERROR_BAD_EXE_FORMAT = 193


class SteamAPILoadError(Exception):
    pass


class SteamAPINotFoundError(SteamAPILoadError):
    pass


def load_steam_api(path: str | os.PathLike[str]) -> ctypes.CDLL:
    """Load steam_api64.dll from `path`, which may be the DLL itself or its directory."""
    dll_path = Path(path)
    if dll_path.is_dir():
        dll_path = dll_path / STEAM_API_DLL

    if not dll_path.is_file():
        raise SteamAPINotFoundError(f"{STEAM_API_DLL} not found at {dll_path}")

    # ctypes only adds the DLL's own directory to the dependency search path
    # when it is given a path rather than a bare name, so always pass the full path.
    dll_path = dll_path.resolve()

    try:
        return ctypes.CDLL(str(dll_path))
    except OSError as exc:
        message = f"could not load {dll_path}: {exc}"
        if getattr(exc, "winerror", None) == _ERROR_BAD_EXE_FORMAT:
            message += " (check that the file is intact and that Python is 64-bit)"
        raise SteamAPILoadError(message) from exc
