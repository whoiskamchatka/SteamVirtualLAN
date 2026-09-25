import ctypes
from unittest import mock

import pytest

from steamlan.steam import SteamAPILoadError
from steamlan.steam.native import (
    GAME_LOBBY_JOIN_REQUESTED,
    LOBBY_CHAT_UPDATE,
    LOBBY_CREATED,
    LOBBY_ENTER,
    CallbackMsg,
    ChatMemberStateChange,
    ChatRoomEnterResponse,
    GameLobbyJoinRequested,
    HSteamPipe,
    LobbyChatUpdate,
    LobbyCreated,
    LobbyEnter,
    LobbyType,
    SteamAPICallCompleted,
    SteamErrMsg,
    bind,
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


def test_invite_and_join_signatures():
    lib = bind(mock.Mock())

    assert lib.SteamAPI_ISteamFriends_ActivateGameOverlayInviteDialog.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint64,
    ]
    assert lib.SteamAPI_ISteamFriends_ActivateGameOverlayInviteDialog.restype is None
    assert lib.SteamAPI_ISteamMatchmaking_JoinLobby.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint64,
    ]
    assert lib.SteamAPI_ISteamMatchmaking_JoinLobby.restype is ctypes.c_uint64
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


def test_lobby_enter_layout():
    assert ctypes.sizeof(LobbyEnter) == 24
    assert offsets(LobbyEnter) == [0, 8, 12, 16]
    assert ctypes.sizeof(ctypes.c_bool) == 1


def test_lobby_chat_update_layout():
    assert ctypes.sizeof(LobbyChatUpdate) == 32
    assert offsets(LobbyChatUpdate) == [0, 8, 16, 24]


def test_game_lobby_join_requested_layout():
    assert ctypes.sizeof(GameLobbyJoinRequested) == 16
    assert offsets(GameLobbyJoinRequested) == [0, 8]


def test_chat_member_state_change_values():
    assert [(flag.name, flag.value) for flag in ChatMemberStateChange] == [
        ("ENTERED", 0x01),
        ("LEFT", 0x02),
        ("DISCONNECTED", 0x04),
        ("KICKED", 0x08),
        ("BANNED", 0x10),
    ]


def test_chat_room_enter_response_values():
    assert ChatRoomEnterResponse.SUCCESS == 1
    assert ChatRoomEnterResponse.DOESNT_EXIST == 2
    assert ChatRoomEnterResponse.FULL == 4
    assert ChatRoomEnterResponse.RATELIMIT_EXCEEDED == 15


def test_missing_export():
    lib = mock.Mock(spec=["SteamAPI_InitFlat"])

    with pytest.raises(SteamAPILoadError, match="unsupported"):
        bind(lib)
