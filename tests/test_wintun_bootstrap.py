"""The Wintun download and cache, without touching the network or the real repository."""

import hashlib
import io
import zipfile

import pytest

from steamlan.adapter import bootstrap
from steamlan.adapter import wintun as wintun_module
from steamlan.adapter.wintun import WintunLoadError

DLL = b"MZ official amd64 wintun.dll stand-in"


def make_package(files=None):
    files = files if files is not None else {"wintun/bin/amd64/wintun.dll": DLL}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("wintun/LICENSE.txt", "Prebuilt Binaries License")
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


PACKAGE = make_package()


@pytest.fixture(autouse=True)
def official(tmp_path, monkeypatch):
    """Pretend PACKAGE and DLL are the official files, and keep the cache in tmp_path."""
    package_hash = hashlib.sha256(PACKAGE).hexdigest()
    dll_hashes = {"amd64": hashlib.sha256(DLL).hexdigest(), "arm64": "0" * 64}
    monkeypatch.setattr(bootstrap, "PACKAGE_SHA256", package_hash)
    monkeypatch.setattr(bootstrap, "DLL_SHA256", dll_hashes)
    monkeypatch.setattr(wintun_module, "DLL_SHA256", dll_hashes)
    monkeypatch.setattr(wintun_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(wintun_module, "wintun_arch", lambda: "amd64")
    monkeypatch.delenv("STEAMLAN_WINTUN", raising=False)
    return tmp_path


def cached(tmp_path):
    return tmp_path / "wintun" / "bin" / "amd64" / "wintun.dll"


class FakeResponse:
    def __init__(self, data=PACKAGE, url="https://www.wintun.net/builds/wintun-0.14.1.zip"):
        self.data = data
        self.url = url

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.data[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_download_package():
    requests = []

    def urlopen(url, timeout):
        requests.append((url, timeout))
        return FakeResponse()

    assert bootstrap.download_package(urlopen=urlopen) == PACKAGE
    assert requests == [("https://www.wintun.net/builds/wintun-0.14.1.zip", 30)]


@pytest.mark.parametrize(
    "url",
    [
        "http://www.wintun.net/builds/wintun-0.14.1.zip",
        "https://example.com/wintun-0.14.1.zip",
        "https://www.wintun.net.example.com/wintun-0.14.1.zip",
    ],
)
def test_download_only_from_official_https_source(url):
    with pytest.raises(WintunLoadError, match="refusing to download"):
        bootstrap.download_package(url, urlopen=lambda *a, **k: pytest.fail("downloaded"))


def test_download_redirected_elsewhere():
    def urlopen(url, timeout):
        return FakeResponse(url="https://mirror.example.com/wintun-0.14.1.zip")

    with pytest.raises(WintunLoadError, match="redirected"):
        bootstrap.download_package(urlopen=urlopen)


def test_download_failure():
    def urlopen(url, timeout):
        raise OSError("getaddrinfo failed")

    with pytest.raises(WintunLoadError, match="could not download.*getaddrinfo"):
        bootstrap.download_package(urlopen=urlopen)


def test_download_too_large():
    def urlopen(url, timeout):
        return FakeResponse(data=b"x" * (bootstrap._MAX_PACKAGE_SIZE + 10))

    with pytest.raises(WintunLoadError, match="larger"):
        bootstrap.download_package(urlopen=urlopen)


def test_verify_package():
    bootstrap.verify_package(PACKAGE)

    with pytest.raises(WintunLoadError, match="was not used"):
        bootstrap.verify_package(PACKAGE + b"tampered")


def test_extract_only_the_dll_for_this_architecture():
    assert bootstrap.extract_dll(PACKAGE, "amd64") == DLL


def test_extract_missing_architecture():
    with pytest.raises(WintunLoadError, match="no wintun/bin/arm64/wintun.dll"):
        bootstrap.extract_dll(PACKAGE, "arm64")


def test_extract_unexpected_dll():
    package = make_package({"wintun/bin/amd64/wintun.dll": b"something else"})

    with pytest.raises(WintunLoadError, match="not the expected build"):
        bootstrap.extract_dll(package, "amd64")


def test_extract_broken_zip():
    with pytest.raises(WintunLoadError, match="could not be opened"):
        bootstrap.extract_dll(b"not a zip", "amd64")


def test_install_dll(tmp_path):
    path = tmp_path / "wintun" / "bin" / "amd64" / "wintun.dll"

    bootstrap.install_dll(DLL, path)

    assert path.read_bytes() == DLL
    assert list(path.parent.iterdir()) == [path]


def test_install_dll_failure_leaves_nothing_behind(tmp_path):
    path = tmp_path / "wintun.dll"
    path.mkdir()

    with pytest.raises(WintunLoadError, match="could not write"):
        bootstrap.install_dll(DLL, path)
    assert not (tmp_path / "wintun.tmp").exists()


def test_ensure_downloads_verifies_and_caches(official):
    progress = []

    path = bootstrap.ensure_wintun(progress.append, download=lambda: PACKAGE)

    assert path == cached(official)
    assert path.read_bytes() == DLL
    assert progress == [
        "Downloading networking component...",
        "Verified.",
        "Installing networking component...",
    ]
    assert [p.name for p in path.parent.iterdir()] == ["wintun.dll"]


def test_ensure_uses_the_cache_without_downloading(official):
    bootstrap.install_dll(DLL, cached(official))
    progress = []

    path = bootstrap.ensure_wintun(progress.append, download=lambda: pytest.fail("downloaded"))

    assert path == cached(official)
    assert progress == []


def test_ensure_replaces_a_damaged_cache(official):
    cached(official).parent.mkdir(parents=True)
    cached(official).write_bytes(b"truncated")

    path = bootstrap.ensure_wintun(lambda message: None, download=lambda: PACKAGE)

    assert path.read_bytes() == DLL


def test_ensure_never_installs_an_unverified_download(official):
    with pytest.raises(WintunLoadError, match="was not used"):
        bootstrap.ensure_wintun(lambda message: None, download=lambda: PACKAGE + b"x")

    assert not (official / "wintun").exists()


def test_ensure_stops_when_the_download_fails(official):
    def download():
        raise WintunLoadError("could not download")

    with pytest.raises(WintunLoadError, match="could not download"):
        bootstrap.ensure_wintun(lambda message: None, download=download)
    assert not (official / "wintun").exists()


def test_ensure_rejects_an_unofficial_configured_dll(official, monkeypatch):
    other = official / "other" / "wintun.dll"
    other.parent.mkdir()
    other.write_bytes(b"someone else's build")
    monkeypatch.setenv("STEAMLAN_WINTUN", str(other))

    with pytest.raises(WintunLoadError, match="not the official"):
        bootstrap.ensure_wintun(lambda message: None, download=lambda: pytest.fail("downloaded"))


def test_ensure_accepts_an_official_configured_dll(official, monkeypatch):
    other = official / "other" / "wintun.dll"
    other.parent.mkdir()
    other.write_bytes(DLL)
    monkeypatch.setenv("STEAMLAN_WINTUN", str(other.parent))

    assert bootstrap.ensure_wintun(lambda m: None, download=lambda: pytest.fail("dl")) == other


def test_ensure_unknown_architecture():
    with pytest.raises(WintunLoadError, match="no build for sparc"):
        bootstrap.ensure_wintun(lambda message: None, arch="sparc")
