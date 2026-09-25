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


# Only the EResult values Valve documents for the calls SteamLAN makes.
class EResult(enum.IntEnum):
    OK = 1
    FAIL = 2
    NO_CONNECTION = 3
    INVALID_PARAM = 8
    INVALID_STATE = 11
    ACCESS_DENIED = 15
    TIMEOUT = 16
    LIMIT_EXCEEDED = 25
    IGNORED = 41


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


class LobbyEnter(ctypes.Structure):
    _fields_ = [
        ("m_ulSteamIDLobby", ctypes.c_uint64),
        ("m_rgfChatPermissions", ctypes.c_uint32),
        # A one-byte C++ bool; the uint32 after it is padded to offset 16.
        ("m_bLocked", ctypes.c_bool),
        ("m_EChatRoomEnterResponse", ctypes.c_uint32),
    ]


LOBBY_ENTER = 504  # LobbyEnter_t::k_iCallback


class ChatRoomEnterResponse(enum.IntEnum):
    SUCCESS = 1
    DOESNT_EXIST = 2
    NOT_ALLOWED = 3
    FULL = 4
    ERROR = 5
    BANNED = 6
    LIMITED = 7
    COMMUNITY_BAN = 9
    MEMBER_BLOCKED_YOU = 10
    YOU_BLOCKED_MEMBER = 11
    RATELIMIT_EXCEEDED = 15


# Both fields are CSteamID, a union with a uint64 declared under pack(1). Only
# alignment differs from uint64, which doesn't change this struct's layout.
class GameLobbyJoinRequested(ctypes.Structure):
    _fields_ = [
        ("m_steamIDLobby", ctypes.c_uint64),
        ("m_steamIDFriend", ctypes.c_uint64),
    ]


GAME_LOBBY_JOIN_REQUESTED = 333  # GameLobbyJoinRequested_t::k_iCallback


# The friend is a CSteamID (see GameLobbyJoinRequested), followed by a
# char[k_cchMaxRichPresenceValueLength] connect string.
class GameRichPresenceJoinRequested(ctypes.Structure):
    _fields_ = [
        ("m_steamIDFriend", ctypes.c_uint64),
        ("m_rgchConnect", ctypes.c_char * 256),
    ]


GAME_RICH_PRESENCE_JOIN_REQUESTED = 337  # GameRichPresenceJoinRequested_t::k_iCallback


HSteamNetConnection = ctypes.c_uint32
HSteamListenSocket = ctypes.c_uint32
SteamNetworkingPOPID = ctypes.c_uint32

IDENTITY_TYPE_STEAM_ID = 16  # k_ESteamNetworkingIdentityType_SteamID


# steamnetworkingtypes.h declares this under #pragma pack(1). The union holding
# the actual identity is 128 bytes; only the SteamID member is read here.
class SteamNetworkingIdentity(ctypes.Structure):
    _pack_ = 1
    _layout_ = "ms"
    _fields_ = [
        ("m_eType", ctypes.c_int),
        ("m_cbSize", ctypes.c_int),
        ("m_data", ctypes.c_uint8 * 128),
    ]


def identity_steam_id(identity: SteamNetworkingIdentity) -> int:
    """Same as SteamNetworkingIdentity::GetSteamID64(): 0 unless it is a SteamID."""
    if identity.m_eType != IDENTITY_TYPE_STEAM_ID:
        return 0
    return int.from_bytes(bytes(identity.m_data[:8]), "little")


# Also #pragma pack(1): a 16-byte IPv6 address followed by the port.
class SteamNetworkingIPAddr(ctypes.Structure):
    _pack_ = 1
    _layout_ = "ms"
    _fields_ = [
        ("m_ipv6", ctypes.c_uint8 * 16),
        ("m_port", ctypes.c_uint16),
    ]


class ConnectionState(enum.IntEnum):
    NONE = 0
    CONNECTING = 1
    FINDING_ROUTE = 2
    CONNECTED = 3
    CLOSED_BY_PEER = 4
    PROBLEM_DETECTED_LOCALLY = 5
    FIN_WAIT = -1
    LINGER = -2
    DEAD = -3


class SteamNetConnectionInfo(ctypes.Structure):
    _fields_ = [
        ("m_identityRemote", SteamNetworkingIdentity),
        ("m_nUserData", ctypes.c_int64),
        ("m_hListenSocket", HSteamListenSocket),
        ("m_addrRemote", SteamNetworkingIPAddr),
        ("m__pad1", ctypes.c_uint16),
        ("m_idPOPRemote", SteamNetworkingPOPID),
        ("m_idPOPRelay", SteamNetworkingPOPID),
        ("m_eState", ctypes.c_int),
        ("m_eEndReason", ctypes.c_int),
        ("m_szEndDebug", ctypes.c_char * 128),
        ("m_szConnectionDescription", ctypes.c_char * 128),
        ("m_nFlags", ctypes.c_int),
        ("reserved", ctypes.c_uint32 * 63),
    ]


class SteamNetConnectionStatusChangedCallback(ctypes.Structure):
    _fields_ = [
        ("m_hConn", HSteamNetConnection),
        ("m_info", SteamNetConnectionInfo),
        ("m_eOldState", ctypes.c_int),
    ]


# SteamNetConnectionStatusChangedCallback_t::k_iCallback
CONNECTION_STATUS_CHANGED = 1221

# k_HSteamNetConnection_Invalid and k_HSteamListenSocket_Invalid
NET_HANDLE_INVALID = 0

SEND_RELIABLE = 8  # k_nSteamNetworkingSend_Reliable
# k_nSteamNetworkingSend_UnreliableNoNagle: may be dropped, and sent right away
# instead of waiting to be combined with later messages.
SEND_UNRELIABLE_NO_NAGLE = 1


# Declared outside any #pragma pack, so it uses the default x64 layout. Steam
# owns received messages; they must be released, never freed directly.
class SteamNetworkingMessage(ctypes.Structure):
    _fields_ = [
        ("m_pData", ctypes.c_void_p),
        ("m_cbSize", ctypes.c_int),
        ("m_conn", HSteamNetConnection),
        ("m_identityPeer", SteamNetworkingIdentity),
        ("m_nConnUserData", ctypes.c_int64),
        ("m_usecTimeReceived", ctypes.c_int64),
        ("m_nMessageNumber", ctypes.c_int64),
        ("m_pfnFreeData", ctypes.c_void_p),
        ("m_pfnRelease", ctypes.c_void_p),
        ("m_nChannel", ctypes.c_int),
        ("m_nFlags", ctypes.c_int),
        ("m_nUserData", ctypes.c_int64),
        ("m_idxLane", ctypes.c_uint16),
        ("_pad1__", ctypes.c_uint16),
    ]


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

        lib.SteamAPI_ISteamMatchmaking_JoinLobby.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.SteamAPI_ISteamMatchmaking_JoinLobby.restype = SteamAPICall_t

        lib.SteamAPI_ISteamMatchmaking_SetLobbyData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        lib.SteamAPI_ISteamMatchmaking_SetLobbyData.restype = ctypes.c_bool

        # Steam owns the returned strings; c_char_p copies them on return.
        lib.SteamAPI_ISteamMatchmaking_GetLobbyData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_char_p,
        ]
        lib.SteamAPI_ISteamMatchmaking_GetLobbyData.restype = ctypes.c_char_p

        lib.SteamAPI_ISteamMatchmaking_GetLobbyOwner.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.SteamAPI_ISteamMatchmaking_GetLobbyOwner.restype = ctypes.c_uint64

        lib.SteamAPI_ISteamFriends_GetFriendPersonaName.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
        ]
        lib.SteamAPI_ISteamFriends_GetFriendPersonaName.restype = ctypes.c_char_p

        lib.SteamAPI_ISteamFriends_ActivateGameOverlayInviteDialogConnectString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
        ]
        lib.SteamAPI_ISteamFriends_ActivateGameOverlayInviteDialogConnectString.restype = None

        lib.SteamAPI_SteamUtils_v011.argtypes = []
        lib.SteamAPI_SteamUtils_v011.restype = ctypes.c_void_p

        lib.SteamAPI_ISteamUtils_IsOverlayEnabled.argtypes = [ctypes.c_void_p]
        lib.SteamAPI_ISteamUtils_IsOverlayEnabled.restype = ctypes.c_bool

        lib.SteamAPI_ISteamUtils_BOverlayNeedsPresent.argtypes = [ctypes.c_void_p]
        lib.SteamAPI_ISteamUtils_BOverlayNeedsPresent.restype = ctypes.c_bool

        lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.argtypes = []
        lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.restype = ctypes.c_void_p

        lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(SteamNetworkingIdentity),
        ]
        lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.restype = ctypes.c_bool

        lib.SteamAPI_SteamNetworkingIdentity_SetSteamID64.argtypes = [
            ctypes.POINTER(SteamNetworkingIdentity),
            ctypes.c_uint64,
        ]
        lib.SteamAPI_SteamNetworkingIdentity_SetSteamID64.restype = None

        # The config option arguments are always passed as 0 and NULL.
        lib.SteamAPI_ISteamNetworkingSockets_CreateListenSocketP2P.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        lib.SteamAPI_ISteamNetworkingSockets_CreateListenSocketP2P.restype = HSteamListenSocket

        # identityRemote is a C++ reference, passed as a pointer.
        lib.SteamAPI_ISteamNetworkingSockets_ConnectP2P.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(SteamNetworkingIdentity),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        lib.SteamAPI_ISteamNetworkingSockets_ConnectP2P.restype = HSteamNetConnection

        lib.SteamAPI_ISteamNetworkingSockets_AcceptConnection.argtypes = [
            ctypes.c_void_p,
            HSteamNetConnection,
        ]
        lib.SteamAPI_ISteamNetworkingSockets_AcceptConnection.restype = ctypes.c_int

        lib.SteamAPI_ISteamNetworkingSockets_CloseConnection.argtypes = [
            ctypes.c_void_p,
            HSteamNetConnection,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_bool,
        ]
        lib.SteamAPI_ISteamNetworkingSockets_CloseConnection.restype = ctypes.c_bool

        lib.SteamAPI_ISteamNetworkingSockets_CloseListenSocket.argtypes = [
            ctypes.c_void_p,
            HSteamListenSocket,
        ]
        lib.SteamAPI_ISteamNetworkingSockets_CloseListenSocket.restype = ctypes.c_bool

        lib.SteamAPI_ISteamNetworkingSockets_SendMessageToConnection.argtypes = [
            ctypes.c_void_p,
            HSteamNetConnection,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int64),
        ]
        lib.SteamAPI_ISteamNetworkingSockets_SendMessageToConnection.restype = ctypes.c_int

        lib.SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnConnection.argtypes = [
            ctypes.c_void_p,
            HSteamNetConnection,
            ctypes.POINTER(ctypes.POINTER(SteamNetworkingMessage)),
            ctypes.c_int,
        ]
        lib.SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnConnection.restype = ctypes.c_int

        lib.SteamAPI_SteamNetworkingMessage_t_Release.argtypes = [
            ctypes.POINTER(SteamNetworkingMessage)
        ]
        lib.SteamAPI_SteamNetworkingMessage_t_Release.restype = None
    except AttributeError as exc:
        raise SteamAPILoadError(f"unsupported {STEAM_API_DLL}: {exc}") from exc

    return lib


# Same as the header's inline SteamAPI_SteamUser(), SteamAPI_SteamFriends(),
# SteamAPI_SteamMatchmaking(), SteamAPI_SteamUtils() and
# SteamAPI_SteamNetworkingSockets_SteamAPI(): the exported accessors are
# versioned, so the versions live only here.
def steam_user(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamUser_v023()


def steam_friends(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamFriends_v018()


def steam_matchmaking(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamMatchmaking_v009()


def steam_utils(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamUtils_v011()


def steam_networking_sockets(lib: ctypes.CDLL) -> int | None:
    return lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013()
