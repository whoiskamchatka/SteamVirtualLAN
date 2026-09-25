import ctypes
import enum

from steamlan.steam.loader import STEAM_API_DLL, SteamAPILoadError

# typedef char SteamErrMsg[1024]
SteamErrMsg = ctypes.c_char * 1024


class SteamAPIInitResult(enum.IntEnum):
    OK = 0
    FAILED_GENERIC = 1
    NO_STEAM_CLIENT = 2
    VERSION_MISMATCH = 3


def bind(lib: ctypes.CDLL) -> ctypes.CDLL:
    """Declare signatures for the Steamworks flat API functions SteamLAN calls."""
    try:
        # ESteamAPIInitResult SteamAPI_InitFlat(SteamErrMsg *pOutErrMsg)
        # The enum has no explicit underlying type, so it is a C int.
        lib.SteamAPI_InitFlat.argtypes = [ctypes.POINTER(SteamErrMsg)]
        lib.SteamAPI_InitFlat.restype = ctypes.c_int

        lib.SteamAPI_Shutdown.argtypes = []
        lib.SteamAPI_Shutdown.restype = None
    except AttributeError as exc:
        raise SteamAPILoadError(f"unsupported {STEAM_API_DLL}: {exc}") from exc

    return lib
