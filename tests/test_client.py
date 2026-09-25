from unittest import mock

import pytest

from steamlan.steam import (
    SteamAPILoadError,
    SteamAPINotFoundError,
    SteamClient,
    SteamError,
    SteamInitError,
)
from steamlan.steam.native import SteamAPIInitResult, SteamErrMsg

DLL_PATH = r"C:\steam\steam_api64.dll"
USER = 0x1000
FRIENDS = 0x2000
STEAM_ID = 76561197960265729


def init_result(result, message=b""):
    def init(err):
        err.value = message
        return result

    return init


@pytest.fixture
def lib():
    lib = mock.Mock()
    lib.SteamAPI_InitFlat.side_effect = init_result(SteamAPIInitResult.OK)
    lib.SteamAPI_SteamUser_v023.return_value = USER
    lib.SteamAPI_ISteamUser_GetSteamID.return_value = STEAM_ID
    lib.SteamAPI_SteamFriends_v018.return_value = FRIENDS
    lib.SteamAPI_ISteamFriends_GetPersonaName.return_value = b"Test User"
    return lib


@pytest.fixture
def load(lib):
    with mock.patch("steamlan.steam.client.load_steam_api", return_value=lib) as load:
        yield load


@pytest.fixture
def failing_lib(lib):
    lib.SteamAPI_InitFlat.side_effect = init_result(
        SteamAPIInitResult.NO_STEAM_CLIENT, b"Steam is not running."
    )
    return lib


def test_construction_does_not_load(load):
    steam = SteamClient(DLL_PATH)

    assert not steam.running
    load.assert_not_called()


def test_start(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()

    load.assert_called_once_with(DLL_PATH)
    lib.SteamAPI_InitFlat.assert_called_once()
    (err,) = lib.SteamAPI_InitFlat.call_args.args
    assert isinstance(err, SteamErrMsg)
    assert steam.running


def test_start_twice_initializes_once(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.start()

    lib.SteamAPI_InitFlat.assert_called_once()


def test_init_failure(load, failing_lib):
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamInitError, match="Steam is not running."):
        steam.start()

    assert not steam.running


def test_init_failure_without_message(load, lib):
    lib.SteamAPI_InitFlat.side_effect = init_result(SteamAPIInitResult.VERSION_MISMATCH)
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamInitError, match="initialization failed: VERSION_MISMATCH"):
        steam.start()

    assert not steam.running


def test_init_failure_with_unknown_result(load, lib):
    lib.SteamAPI_InitFlat.side_effect = init_result(99)
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamInitError, match="result 99"):
        steam.start()


def test_close_after_init_failure_does_not_shut_down(load, failing_lib):
    steam = SteamClient(DLL_PATH)
    with pytest.raises(SteamInitError):
        steam.start()

    steam.close()

    failing_lib.SteamAPI_Shutdown.assert_not_called()


def test_start_after_init_failure(load, failing_lib):
    steam = SteamClient(DLL_PATH)
    with pytest.raises(SteamInitError):
        steam.start()

    failing_lib.SteamAPI_InitFlat.side_effect = init_result(SteamAPIInitResult.OK)
    steam.start()

    assert steam.running


def test_loader_error_propagates(load, lib):
    error = SteamAPINotFoundError("steam_api64.dll not found")
    load.side_effect = error
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamAPINotFoundError) as excinfo:
        steam.start()

    assert excinfo.value is error
    assert not steam.running
    lib.SteamAPI_InitFlat.assert_not_called()


def test_missing_export_is_a_load_error(load):
    load.return_value = mock.Mock(spec=[])
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamAPILoadError):
        steam.start()

    assert not steam.running


def test_close(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.close()

    lib.SteamAPI_Shutdown.assert_called_once_with()
    assert not steam.running


def test_close_twice(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.close()
    steam.close()

    lib.SteamAPI_Shutdown.assert_called_once_with()


def test_close_before_start(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.close()

    load.assert_not_called()
    lib.SteamAPI_Shutdown.assert_not_called()
    assert not steam.running


def test_context_manager(load, lib):
    with SteamClient(DLL_PATH) as steam:
        assert steam.running
        lib.SteamAPI_Shutdown.assert_not_called()

    lib.SteamAPI_Shutdown.assert_called_once_with()
    assert not steam.running


def test_context_manager_shuts_down_on_error(load, lib):
    steam = SteamClient(DLL_PATH)

    with pytest.raises(ValueError), steam:
        raise ValueError

    lib.SteamAPI_Shutdown.assert_called_once_with()
    assert not steam.running


def test_context_manager_init_failure(load, failing_lib):
    with pytest.raises(SteamInitError), SteamClient(DLL_PATH):
        pytest.fail("body should not run")

    failing_lib.SteamAPI_Shutdown.assert_not_called()


@pytest.fixture
def steam(load):
    with SteamClient(DLL_PATH) as steam:
        yield steam


def test_steam_id(steam, lib):
    assert steam.steam_id == STEAM_ID
    assert type(steam.steam_id) is int
    lib.SteamAPI_ISteamUser_GetSteamID.assert_called_with(USER)


def test_steam_id_without_user_interface(steam, lib):
    lib.SteamAPI_SteamUser_v023.return_value = None

    with pytest.raises(SteamError, match="ISteamUser"):
        _ = steam.steam_id


def test_empty_steam_id(steam, lib):
    lib.SteamAPI_ISteamUser_GetSteamID.return_value = 0

    with pytest.raises(SteamError, match="empty SteamID"):
        _ = steam.steam_id


def test_persona_name(steam, lib):
    assert steam.persona_name == "Test User"
    lib.SteamAPI_ISteamFriends_GetPersonaName.assert_called_with(FRIENDS)


def test_persona_name_is_utf8(steam, lib):
    lib.SteamAPI_ISteamFriends_GetPersonaName.return_value = "Jürgen 猫".encode()

    assert steam.persona_name == "Jürgen 猫"


def test_persona_name_is_not_cached(steam, lib):
    assert steam.persona_name == "Test User"
    lib.SteamAPI_ISteamFriends_GetPersonaName.return_value = b"Renamed"

    assert steam.persona_name == "Renamed"


def test_persona_name_without_friends_interface(steam, lib):
    lib.SteamAPI_SteamFriends_v018.return_value = None

    with pytest.raises(SteamError, match="ISteamFriends"):
        _ = steam.persona_name


def test_null_persona_name(steam, lib):
    lib.SteamAPI_ISteamFriends_GetPersonaName.return_value = None

    with pytest.raises(SteamError, match="no persona name"):
        _ = steam.persona_name


@pytest.mark.parametrize("attr", ["steam_id", "persona_name"])
def test_identity_before_start(load, attr):
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamError, match="not running"):
        getattr(steam, attr)

    load.assert_not_called()
    assert not steam.running


@pytest.mark.parametrize("attr", ["steam_id", "persona_name"])
def test_identity_after_close(load, lib, attr):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.close()

    with pytest.raises(SteamError, match="not running"):
        getattr(steam, attr)

    lib.SteamAPI_Shutdown.assert_called_once_with()


@pytest.mark.parametrize("attr", ["steam_id", "persona_name"])
def test_identity_after_init_failure(load, failing_lib, attr):
    steam = SteamClient(DLL_PATH)
    with pytest.raises(SteamInitError):
        steam.start()

    with pytest.raises(SteamError, match="not running"):
        getattr(steam, attr)
