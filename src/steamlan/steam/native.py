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


HSteamPipe = ctypes.c_int32
SteamAPICall_t = ctypes.c_uint64


# Windows builds of the SDK use #pragma pack(8) for callback structs
# (VALVE_CALLBACK_PACK_LARGE), which is the same as ctypes' natural alignment
# for these fields.
class CallbackMsg(ctypes.Structure):
    _fields_ = [
        ("m_hSteamUser", ctypes.c_int32),
        ("m_iCallback", ctypes.c_int),
        ("m_pubParam", ctypes.POINTER(ctypes.c_uint8)),
        ("m_cubParam", ctypes.c_int),
    ]


class SteamAPICallCompleted(ctypes.Structure):
    _fields_ = [
        ("m_hAsyncCall", SteamAPICall_t),
        ("m_iCallback", ctypes.c_int),
        ("m_cubParam", ctypes.c_uint32),
    ]


STEAM_API_CALL_COMPLETED = 703  # SteamAPICallCompleted_t::k_iCallback

# SteamAPICall_t returned when a call could not be started.
API_CALL_INVALID = 0


class LobbyType(enum.IntEnum):
    PRIVATE = 0
    FRIENDS_ONLY = 1
    PUBLIC = 2
    INVISIBLE = 3


# Only the EResult values Valve documents for LobbyCreated_t.
class EResult(enum.IntEnum):
    OK = 1
    FAIL = 2
    NO_CONNECTION = 3
    ACCESS_DENIED = 15
    TIMEOUT = 16
    LIMIT_EXCEEDED = 25


# EResult is a C int; under pack(8) the uint64 after it is 8-byte aligned,
# so the struct is 16 bytes.
class LobbyCreated(ctypes.Structure):
    _fields_ = [
        ("m_eResult", ctypes.c_int),
        ("m_ulSteamIDLobby", ctypes.c_uint64),
    ]


LOBBY_CREATED = 513  # LobbyCreated_t::k_iCallback


class LobbyChatUpdate(ctypes.Structure):
    _fields_ = [
        ("m_ulSteamIDLobby", ctypes.c_uint64),
        ("m_ulSteamIDUserChanged", ctypes.c_uint64),
        ("m_ulSteamIDMakingChange", ctypes.c_uint64),
        ("m_rgfChatMemberStateChange", ctypes.c_uint32),
    ]


LOBBY_CHAT_UPDATE = 506  # LobbyChatUpdate_t::k_iCallback


class ChatMemberStateChange(enum.IntFlag):
    ENTERED = 0x01
    LEFT = 0x02
    DISCONNECTED = 0x04
    KICKED = 0x08
    BANNED = 0x10


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

        lib.SteamAPI_GetHSteamPipe.argtypes = []
        lib.SteamAPI_GetHSteamPipe.restype = HSteamPipe

        lib.SteamAPI_ManualDispatch_Init.argtypes = []
        lib.SteamAPI_ManualDispatch_Init.restype = None

        lib.SteamAPI_ManualDispatch_RunFrame.argtypes = [HSteamPipe]
        lib.SteamAPI_ManualDispatch_RunFrame.restype = None

        lib.SteamAPI_ManualDispatch_GetNextCallback.argtypes = [
            HSteamPipe,
            ctypes.POINTER(CallbackMsg),
        ]
        lib.SteamAPI_ManualDispatch_GetNextCallback.restype = ctypes.c_bool

        lib.SteamAPI_ManualDispatch_FreeLastCallback.argtypes = [HSteamPipe]
        lib.SteamAPI_ManualDispatch_FreeLastCallback.restype = None

        lib.SteamAPI_ManualDispatch_GetAPICallResult.argtypes = [
            HSteamPipe,
            SteamAPICall_t,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_bool),
        ]
        lib.SteamAPI_ManualDispatch_GetAPICallResult.restype = ctypes.c_bool

        lib.SteamAPI_SteamMatchmaking_v009.argtypes = []
        lib.SteamAPI_SteamMatchmaking_v009.restype = ctypes.c_void_p

        # ELobbyType has no explicit underlying type, so it is a C int.
        lib.SteamAPI_ISteamMatchmaking_CreateLobby.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.SteamAPI_ISteamMatchmaking_CreateLobby.restype = SteamAPICall_t

        lib.SteamAPI_ISteamMatchmaking_LeaveLobby.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.SteamAPI_ISteamMatchmaking_LeaveLobby.restype = None

        lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
        ]
        lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.restype = ctypes.c_int

        lib.SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_int,
        ]
        lib.SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex.restype = ctypes.c_uint64

        lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_uint64,
        ]
        lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby.restype = ctypes.c_bool
    except AttributeError as exc:
        raise SteamAPILoadError(f"unsupported {STEAM_API_DLL}: {exc}") from exc

    return lib


# Same as the header's inline SteamAPI_SteamUser(), SteamAPI_SteamFriends() and
# SteamAPI_SteamMatchmaking(): the exported accessors are versioned, so the
# versions live only here.
def steam_user(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamUser_v023()


def steam_friends(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamFriends_v018()


def steam_matchmaking(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamMatchmaking_v009()
