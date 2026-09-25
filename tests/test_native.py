import ctypes
from unittest import mock

import pytest

from steamlan.steam import SteamAPILoadError
from steamlan.steam.native import (
    CallbackMsg,
    HSteamPipe,
    LobbyCreated,
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


def test_missing_export():
    lib = mock.Mock(spec=["SteamAPI_InitFlat"])

    with pytest.raises(SteamAPILoadError, match="unsupported"):
        bind(lib)
