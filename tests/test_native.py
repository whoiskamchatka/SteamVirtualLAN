import ctypes
from unittest import mock

import pytest

from steamlan.steam import SteamAPILoadError
from steamlan.steam.native import SteamErrMsg, bind


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


def test_missing_export():
    lib = mock.Mock(spec=["SteamAPI_InitFlat"])

    with pytest.raises(SteamAPILoadError, match="unsupported"):
        bind(lib)
