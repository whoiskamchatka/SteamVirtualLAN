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

        # Interface pointers are opaque, so they are handled as c_void_p.
        lib.SteamAPI_SteamUser_v023.argtypes = []
        lib.SteamAPI_SteamUser_v023.restype = ctypes.c_void_p

        # The flat API returns CSteamID as a plain uint64 (uint64_steamid).
        lib.SteamAPI_ISteamUser_GetSteamID.argtypes = [ctypes.c_void_p]
        lib.SteamAPI_ISteamUser_GetSteamID.restype = ctypes.c_uint64

        lib.SteamAPI_SteamFriends_v018.argtypes = []
        lib.SteamAPI_SteamFriends_v018.restype = ctypes.c_void_p

        # Steam owns the returned string and may free it later; c_char_p copies
        # it into a bytes object as soon as the call returns.
        lib.SteamAPI_ISteamFriends_GetPersonaName.argtypes = [ctypes.c_void_p]
        lib.SteamAPI_ISteamFriends_GetPersonaName.restype = ctypes.c_char_p
    except AttributeError as exc:
        raise SteamAPILoadError(f"unsupported {STEAM_API_DLL}: {exc}") from exc

    return lib


# Same as the header's inline SteamAPI_SteamUser() and SteamAPI_SteamFriends():
# the exported accessors are versioned, so the versions live only here.
def steam_user(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamUser_v023()


def steam_friends(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamFriends_v018()
