import os

import pytest

from steamlan.app import steamworks
from steamlan.steam import SteamInitError


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "src" / "steamlan").mkdir(parents=True)
    return tmp_path


def test_creates_steam_appid(tmp_path):
    path = steamworks.ensure_steam_appid(tmp_path)

    assert path == tmp_path / "steam_appid.txt"
    assert path.read_bytes() == b"480\n"


@pytest.mark.parametrize("content", [b"480\n", b"480", b"480\r\n", b"\xef\xbb\xbf480\n"])
def test_correct_file_is_left_alone(tmp_path, content):
    path = tmp_path / "steam_appid.txt"
    path.write_bytes(content)
    os.utime(path, (1_000_000, 1_000_000))

    steamworks.ensure_steam_appid(tmp_path)

    assert path.read_bytes() == content
    assert path.stat().st_mtime == 1_000_000


@pytest.mark.parametrize("content", [b"", b"730\n", b"not an id", b"\xff\xfe4\x008\x000\x00"])
def test_wrong_or_malformed_file_is_replaced(tmp_path, content, caplog):
    path = tmp_path / "steam_appid.txt"
    path.write_bytes(content)

    steamworks.ensure_steam_appid(tmp_path)

    assert path.read_bytes() == b"480\n"
    assert "Replacing" in caplog.text


def test_unwritable_file_is_a_clear_error(tmp_path):
    (tmp_path / "steam_appid.txt").mkdir()

    with pytest.raises(SteamInitError, match="Could not write .*steam_appid.txt"):
        steamworks.ensure_steam_appid(tmp_path)


def test_project_root_detection(project, tmp_path_factory):
    assert steamworks.is_project_root(project)
    assert not steamworks.is_project_root(tmp_path_factory.mktemp("elsewhere"))


def test_prepare_app_id_in_project_root(project, monkeypatch):
    monkeypatch.delenv("SteamAppId", raising=False)

    assert steamworks.prepare_app_id(project) == project / "steam_appid.txt"
    assert (project / "steam_appid.txt").read_text() == "480\n"
    assert "SteamAppId" not in os.environ


def test_prepare_app_id_elsewhere_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SteamAppId", raising=False)

    assert steamworks.prepare_app_id(tmp_path) is None
    assert list(tmp_path.iterdir()) == []
    assert os.environ["SteamAppId"] == "480"


def test_prepare_app_id_uses_working_directory(project, monkeypatch):
    monkeypatch.chdir(project)

    steamworks.prepare_app_id()

    assert (project / "steam_appid.txt").is_file()
