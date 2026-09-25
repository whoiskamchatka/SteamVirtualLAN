"""Getting the official wintun.dll: a bundled or cached copy, or a verified download.

Development machines get it automatically from wintun.net the first time; it is
kept in the ignored wintun directory. A packaged SteamVirtualLAN is meant to
ship the DLL next to its exe instead, as Wintun's documentation describes.
"""

import hashlib
import io
import os
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from steamlan.adapter.wintun import (
    DLL_SHA256,
    PACKAGE_SHA256,
    PACKAGE_URL,
    WINTUN_DLL,
    WintunLoadError,
    cached_dll,
    candidate_paths,
    is_official_dll,
    wintun_arch,
)

_OFFICIAL_HOST = "www.wintun.net"
# The 0.14.1 package is about 750 KB; anything much larger is not it.
_MAX_PACKAGE_SIZE = 8 * 1024 * 1024
_TIMEOUT = 30


def download_package(url: str = PACKAGE_URL, urlopen=urllib.request.urlopen) -> bytes:
    """The Wintun zip from wintun.net over HTTPS (certificates checked by urllib)."""
    if urlparse(url).scheme != "https" or urlparse(url).netloc != _OFFICIAL_HOST:
        raise WintunLoadError(f"refusing to download Wintun from {url}")
    try:
        with urlopen(url, timeout=_TIMEOUT) as response:
            if urlparse(response.geturl()).netloc != _OFFICIAL_HOST:
                raise WintunLoadError(f"the download was redirected to {response.geturl()}")
            package = response.read(_MAX_PACKAGE_SIZE + 1)
    except OSError as exc:
        raise WintunLoadError(f"could not download {url}: {exc}") from exc
    if len(package) > _MAX_PACKAGE_SIZE:
        raise WintunLoadError(f"{url} is larger than the Wintun package can be")
    return package


def verify_package(package: bytes) -> None:
    actual = hashlib.sha256(package).hexdigest()
    if actual != PACKAGE_SHA256:
        raise WintunLoadError(
            f"the downloaded Wintun package has SHA-256 {actual}, expected {PACKAGE_SHA256}; "
            "it was not used"
        )


def extract_dll(package: bytes, arch: str) -> bytes:
    """wintun.dll for arch from a verified package, checked against its own hash."""
    member = f"wintun/bin/{arch}/{WINTUN_DLL}"
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            dll = archive.read(member)
    except KeyError:
        raise WintunLoadError(f"the Wintun package has no {member}") from None
    except zipfile.BadZipFile as exc:
        raise WintunLoadError(f"the Wintun package could not be opened: {exc}") from exc
    if hashlib.sha256(dll).hexdigest() != DLL_SHA256[arch]:
        raise WintunLoadError(f"{member} in the package is not the expected build")
    return dll


def install_dll(dll: bytes, path: Path) -> None:
    """Write the DLL so that a half-written file never has the final name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(dll)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise WintunLoadError(f"could not write {path}: {exc}") from exc


def ensure_wintun(
    progress: Callable[[str], None] = print,
    download: Callable[[], bytes] = download_package,
    arch: str | None = None,
) -> Path:
    """Path of an official wintun.dll for this Python, downloading it if needed."""
    arch = arch or wintun_arch()
    if arch not in DLL_SHA256:
        raise WintunLoadError(f"Wintun has no build for {arch}")

    for path in candidate_paths():
        if path.is_file():
            if is_official_dll(path, arch):
                return path
            if path != cached_dll(arch):
                raise WintunLoadError(f"{path} is not the official Wintun build for {arch}")

    progress("Downloading networking component...")
    package = download()
    verify_package(package)
    progress("Verified.")
    progress("Installing networking component...")
    path = cached_dll(arch)
    install_dll(extract_dll(package, arch), path)
    return path
