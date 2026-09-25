from unittest import mock

import pytest

from steamlan.steam import (
    STEAM_API_DLL,
    SteamAPILoadError,
    SteamAPINotFoundError,
    load_steam_api,
)


@pytest.fixture
def cdll():
    with mock.patch("steamlan.steam.loader.ctypes.CDLL") as cdll:
        yield cdll


@pytest.fixture
def dll(tmp_path):
    path = tmp_path / STEAM_API_DLL
    path.write_bytes(b"")
    return path


def test_loads_dll_from_file_path(cdll, dll):
    lib = load_steam_api(dll)

    cdll.assert_called_once_with(str(dll.resolve()))
    assert lib is cdll.return_value


def test_loads_dll_from_directory(cdll, dll):
    load_steam_api(dll.parent)

    cdll.assert_called_once_with(str(dll.resolve()))


def test_accepts_str_path(cdll, dll):
    load_steam_api(str(dll))

    cdll.assert_called_once_with(str(dll.resolve()))


def test_relative_path_is_resolved(cdll, dll, monkeypatch):
    monkeypatch.chdir(dll.parent)

    load_steam_api(STEAM_API_DLL)

    cdll.assert_called_once_with(str(dll.resolve()))


def test_missing_file(cdll, tmp_path):
    with pytest.raises(SteamAPINotFoundError, match="not found"):
        load_steam_api(tmp_path / STEAM_API_DLL)

    cdll.assert_not_called()


def test_directory_without_dll(cdll, tmp_path):
    with pytest.raises(SteamAPINotFoundError):
        load_steam_api(tmp_path)

    cdll.assert_not_called()


def test_not_found_is_a_load_error(cdll, tmp_path):
    with pytest.raises(SteamAPILoadError):
        load_steam_api(tmp_path / STEAM_API_DLL)


def test_os_error_is_wrapped(cdll, dll):
    error = OSError("[WinError 126] The specified module could not be found")
    cdll.side_effect = error

    with pytest.raises(SteamAPILoadError, match="could not load") as excinfo:
        load_steam_api(dll)

    assert excinfo.value.__cause__ is error
    assert not isinstance(excinfo.value, SteamAPINotFoundError)


def test_bad_exe_format_hint(cdll, dll):
    error = OSError("[WinError 193] %1 is not a valid Win32 application")
    error.winerror = 193
    cdll.side_effect = error

    with pytest.raises(SteamAPILoadError, match="Python is 64-bit"):
        load_steam_api(dll)
