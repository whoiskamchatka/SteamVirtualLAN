import ctypes
from unittest import mock

import pytest

from steamlan.steam import SteamAPILoadError
from steamlan.steam.native import (
    CONNECTION_STATUS_CHANGED,
    GAME_LOBBY_JOIN_REQUESTED,
    GAME_RICH_PRESENCE_JOIN_REQUESTED,
    LOBBY_CHAT_UPDATE,
    LOBBY_CREATED,
    LOBBY_ENTER,
    NET_HANDLE_INVALID,
    SEND_RELIABLE,
    SEND_UNRELIABLE_NO_NAGLE,
    CallbackMsg,
    ChatMemberStateChange,
    ChatRoomEnterResponse,
    ConnectionState,
    EResult,
    GameLobbyJoinRequested,
    GameRichPresenceJoinRequested,
    HSteamPipe,
    LobbyChatUpdate,
    LobbyCreated,
    LobbyEnter,
    LobbyType,
    SteamAPICallCompleted,
    SteamErrMsg,
    SteamNetConnectionInfo,
    SteamNetConnectionStatusChangedCallback,
    SteamNetworkingIdentity,
    SteamNetworkingIPAddr,
    SteamNetworkingMessage,
    bind,
    identity_steam_id,
)


def test_signatures():
    lib = bind(mock.Mock())

    assert lib.SteamAPI_InitFlat.argtypes == [ctypes.POINTER(SteamErrMsg)]
    assert lib.SteamAPI_InitFlat.restype is ctypes.c_int
    assert lib.SteamAPI_Shutdown.argtypes == []
    assert lib.SteamAPI_Shutdown.restype is None


def test_identity_signatures():
    lib = bind(mock.Mock())

    assert lib.SteamAPI_SteamUser_v023.argtypes == []
    assert lib.SteamAPI_SteamUser_v023.restype is ctypes.c_void_p
    assert lib.SteamAPI_ISteamUser_GetSteamID.argtypes == [ctypes.c_void_p]
    assert lib.SteamAPI_ISteamUser_GetSteamID.restype is ctypes.c_uint64

    assert lib.SteamAPI_SteamFriends_v018.argtypes == []
    assert lib.SteamAPI_SteamFriends_v018.restype is ctypes.c_void_p
    assert lib.SteamAPI_ISteamFriends_GetPersonaName.argtypes == [ctypes.c_void_p]
    assert lib.SteamAPI_ISteamFriends_GetPersonaName.restype is ctypes.c_char_p


def test_err_msg_size():
    assert ctypes.sizeof(SteamErrMsg) == 1024


def test_callback_signatures():
    lib = bind(mock.Mock())

    assert lib.SteamAPI_GetHSteamPipe.argtypes == []
    assert lib.SteamAPI_GetHSteamPipe.restype is ctypes.c_int32
    assert lib.SteamAPI_ManualDispatch_Init.argtypes == []
    assert lib.SteamAPI_ManualDispatch_Init.restype is None
    assert lib.SteamAPI_ManualDispatch_RunFrame.argtypes == [HSteamPipe]
    assert lib.SteamAPI_ManualDispatch_RunFrame.restype is None
    assert lib.SteamAPI_ManualDispatch_GetNextCallback.argtypes == [
        HSteamPipe,
        ctypes.POINTER(CallbackMsg),
    ]
    assert lib.SteamAPI_ManualDispatch_GetNextCallback.restype is ctypes.c_bool
    assert lib.SteamAPI_ManualDispatch_FreeLastCallback.argtypes == [HSteamPipe]
    assert lib.SteamAPI_ManualDispatch_FreeLastCallback.restype is None
    assert lib.SteamAPI_ManualDispatch_GetAPICallResult.argtypes == [
        HSteamPipe,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_bool),
    ]
    assert lib.SteamAPI_ManualDispatch_GetAPICallResult.restype is ctypes.c_bool


def offsets(struct):
    return [getattr(struct, name).offset for name, _ in struct._fields_]


def test_callback_msg_layout():
    assert ctypes.sizeof(CallbackMsg) == 24
    assert offsets(CallbackMsg) == [0, 4, 8, 16]


def test_api_call_completed_layout():
    assert ctypes.sizeof(SteamAPICallCompleted) == 16
    assert offsets(SteamAPICallCompleted) == [0, 8, 12]


def test_matchmaking_signatures():
    lib = bind(mock.Mock())

    assert lib.SteamAPI_SteamMatchmaking_v009.argtypes == []
    assert lib.SteamAPI_SteamMatchmaking_v009.restype is ctypes.c_void_p
    assert lib.SteamAPI_ISteamMatchmaking_CreateLobby.argtypes == [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    assert lib.SteamAPI_ISteamMatchmaking_CreateLobby.restype is ctypes.c_uint64
    assert lib.SteamAPI_ISteamMatchmaking_LeaveLobby.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint64,
    ]
    assert lib.SteamAPI_ISteamMatchmaking_LeaveLobby.restype is None
    assert lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint64,
    ]
    assert lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.restype is ctypes.c_int
    assert lib.SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_int,
    ]
    assert lib.SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex.restype is ctypes.c_uint64


def test_lobby_created_layout():
    assert ctypes.sizeof(LobbyCreated) == 16
    assert offsets(LobbyCreated) == [0, 8]


def test_lobby_type_values():
    assert [(t.name, t.value) for t in LobbyType] == [
        ("PRIVATE", 0),
        ("FRIENDS_ONLY", 1),
        ("PUBLIC", 2),
        ("INVISIBLE", 3),
    ]


def test_invite_signature():
    lib = bind(mock.Mock())

    assert lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_uint64,
    ]
    assert lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby.restype is ctypes.c_bool


def test_lobby_callback_ids():
    assert GAME_LOBBY_JOIN_REQUESTED == 300 + 33
    assert LOBBY_ENTER == 500 + 4
    assert LOBBY_CHAT_UPDATE == 500 + 6
    assert LOBBY_CREATED == 500 + 13


def test_lobby_chat_update_layout():
    assert ctypes.sizeof(LobbyChatUpdate) == 32
    assert offsets(LobbyChatUpdate) == [0, 8, 16, 24]


def test_chat_member_state_change_values():
    assert [(flag.name, flag.value) for flag in ChatMemberStateChange] == [
        ("ENTERED", 0x01),
        ("LEFT", 0x02),
        ("DISCONNECTED", 0x04),
        ("KICKED", 0x08),
        ("BANNED", 0x10),
    ]


def test_networking_signatures():
    lib = bind(mock.Mock())

    assert lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.argtypes == []
    assert lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.restype is ctypes.c_void_p
    assert lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.argtypes == [
        ctypes.c_void_p,
        ctypes.POINTER(SteamNetworkingIdentity),
    ]
    assert lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.restype is ctypes.c_bool


def test_networking_identity_layout():
    assert ctypes.sizeof(SteamNetworkingIdentity) == 136
    assert ctypes.alignment(SteamNetworkingIdentity) == 1
    assert offsets(SteamNetworkingIdentity) == [0, 4, 8]


def test_networking_ip_addr_layout():
    assert ctypes.sizeof(SteamNetworkingIPAddr) == 18
    assert ctypes.alignment(SteamNetworkingIPAddr) == 1


def test_connection_info_layout():
    assert ctypes.sizeof(SteamNetConnectionInfo) == 696
    assert ctypes.alignment(SteamNetConnectionInfo) == 8
    assert offsets(SteamNetConnectionInfo) == [
        0,  # m_identityRemote
        136,  # m_nUserData
        144,  # m_hListenSocket
        148,  # m_addrRemote
        166,  # m__pad1
        168,  # m_idPOPRemote
        172,  # m_idPOPRelay
        176,  # m_eState
        180,  # m_eEndReason
        184,  # m_szEndDebug
        312,  # m_szConnectionDescription
        440,  # m_nFlags
        444,  # reserved
    ]


def test_connection_status_changed_layout():
    assert CONNECTION_STATUS_CHANGED == 1220 + 1
    assert ctypes.sizeof(SteamNetConnectionStatusChangedCallback) == 712
    assert offsets(SteamNetConnectionStatusChangedCallback) == [0, 8, 704]


def test_connection_state_values():
    assert {state.name: state.value for state in ConnectionState} == {
        "NONE": 0,
        "CONNECTING": 1,
        "FINDING_ROUTE": 2,
        "CONNECTED": 3,
        "CLOSED_BY_PEER": 4,
        "PROBLEM_DETECTED_LOCALLY": 5,
        "FIN_WAIT": -1,
        "LINGER": -2,
        "DEAD": -3,
    }


def steam_identity(steam_id, identity_type=16):
    identity = SteamNetworkingIdentity(identity_type, 8)
    identity.m_data[:8] = steam_id.to_bytes(8, "little")
    return identity


def test_identity_steam_id():
    assert identity_steam_id(steam_identity(76561197960265729)) == 76561197960265729


@pytest.mark.parametrize("identity_type", [0, 1, 2, 18])
def test_identity_steam_id_other_types(identity_type):
    assert identity_steam_id(steam_identity(76561197960265729, identity_type)) == 0


def test_p2p_signatures():
    lib = bind(mock.Mock())
    uint32, int_, void_p = ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p
    identity_p = ctypes.POINTER(SteamNetworkingIdentity)

    expected = {
        "SteamAPI_SteamNetworkingIdentity_SetSteamID64": ([identity_p, ctypes.c_uint64], None),
        "SteamAPI_ISteamNetworkingSockets_CreateListenSocketP2P": (
            [void_p, int_, int_, void_p],
            uint32,
        ),
        "SteamAPI_ISteamNetworkingSockets_ConnectP2P": (
            [void_p, identity_p, int_, int_, void_p],
            uint32,
        ),
        "SteamAPI_ISteamNetworkingSockets_AcceptConnection": ([void_p, uint32], int_),
        "SteamAPI_ISteamNetworkingSockets_CloseConnection": (
            [void_p, uint32, int_, ctypes.c_char_p, ctypes.c_bool],
            ctypes.c_bool,
        ),
        "SteamAPI_ISteamNetworkingSockets_CloseListenSocket": ([void_p, uint32], ctypes.c_bool),
        "SteamAPI_ISteamNetworkingSockets_SendMessageToConnection": (
            [void_p, uint32, void_p, uint32, int_, ctypes.POINTER(ctypes.c_int64)],
            int_,
        ),
        "SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnConnection": (
            [void_p, uint32, ctypes.POINTER(ctypes.POINTER(SteamNetworkingMessage)), int_],
            int_,
        ),
        "SteamAPI_SteamNetworkingMessage_t_Release": (
            [ctypes.POINTER(SteamNetworkingMessage)],
            None,
        ),
    }
    for name, (argtypes, restype) in expected.items():
        function = getattr(lib, name)
        assert function.argtypes == argtypes, name
        assert function.restype is restype, name


def test_networking_message_layout():
    assert ctypes.sizeof(SteamNetworkingMessage) == 216
    assert ctypes.alignment(SteamNetworkingMessage) == 8
    assert {
        name: getattr(SteamNetworkingMessage, name).offset
        for name, _ in SteamNetworkingMessage._fields_
    } == {
        "m_pData": 0,
        "m_cbSize": 8,
        "m_conn": 12,
        "m_identityPeer": 16,
        "m_nConnUserData": 152,
        "m_usecTimeReceived": 160,
        "m_nMessageNumber": 168,
        "m_pfnFreeData": 176,
        "m_pfnRelease": 184,
        "m_nChannel": 192,
        "m_nFlags": 196,
        "m_nUserData": 200,
        "m_idxLane": 208,
        "_pad1__": 210,
    }


def test_networking_constants():
    assert NET_HANDLE_INVALID == 0
    assert SEND_RELIABLE == 8
    assert SEND_UNRELIABLE_NO_NAGLE == 1
    assert (EResult.OK, EResult.INVALID_PARAM, EResult.INVALID_STATE, EResult.IGNORED) == (
        1,
        8,
        11,
        41,
    )


def test_join_lobby_signature():
    lib = bind(mock.Mock())

    assert lib.SteamAPI_ISteamMatchmaking_JoinLobby.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint64,
    ]
    assert lib.SteamAPI_ISteamMatchmaking_JoinLobby.restype is ctypes.c_uint64


def test_lobby_enter_layout():
    assert ctypes.sizeof(LobbyEnter) == 24
    assert offsets(LobbyEnter) == [0, 8, 12, 16]
    assert ctypes.sizeof(ctypes.c_bool) == 1


def test_game_lobby_join_requested_layout():
    assert ctypes.sizeof(GameLobbyJoinRequested) == 16
    assert offsets(GameLobbyJoinRequested) == [0, 8]


def test_chat_room_enter_response_values():
    assert ChatRoomEnterResponse.SUCCESS == 1
    assert ChatRoomEnterResponse.DOESNT_EXIST == 2
    assert ChatRoomEnterResponse.FULL == 4
    assert ChatRoomEnterResponse.RATELIMIT_EXCEEDED == 15


def test_missing_export():
    lib = mock.Mock(spec=["SteamAPI_InitFlat"])

    with pytest.raises(SteamAPILoadError, match="unsupported"):
        bind(lib)


def test_overlay_signatures():
    lib = bind(mock.Mock())

    invite = lib.SteamAPI_ISteamFriends_ActivateGameOverlayInviteDialogConnectString
    assert invite.argtypes == [ctypes.c_void_p, ctypes.c_char_p]
    assert invite.restype is None
    assert lib.SteamAPI_SteamUtils_v011.argtypes == []
    assert lib.SteamAPI_SteamUtils_v011.restype is ctypes.c_void_p
    for name in (
        "SteamAPI_ISteamUtils_IsOverlayEnabled",
        "SteamAPI_ISteamUtils_BOverlayNeedsPresent",
    ):
        assert getattr(lib, name).argtypes == [ctypes.c_void_p]
        assert getattr(lib, name).restype is ctypes.c_bool


def test_game_rich_presence_join_requested_layout():
    assert GAME_RICH_PRESENCE_JOIN_REQUESTED == 300 + 37
    assert ctypes.sizeof(GameRichPresenceJoinRequested) == 8 + 256
    assert offsets(GameRichPresenceJoinRequested) == [0, 8]


def test_lobby_owner_signatures():
    lib = bind(mock.Mock())

    assert lib.SteamAPI_ISteamMatchmaking_GetLobbyOwner.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint64,
    ]
    assert lib.SteamAPI_ISteamMatchmaking_GetLobbyOwner.restype is ctypes.c_uint64
    assert lib.SteamAPI_ISteamMatchmaking_SetLobbyOwner.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_uint64,
    ]
    assert lib.SteamAPI_ISteamMatchmaking_SetLobbyOwner.restype is ctypes.c_bool
