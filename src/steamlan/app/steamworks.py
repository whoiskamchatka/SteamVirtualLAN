"""Where SteamVirtualLAN finds Steam: the local Steam DLL and the app ID.

steam_api64.dll is Valve's and is never committed. A packaged
SteamVirtualLAN.exe expects it next to itself; during development it goes in
the repository root, the directory with pyproject.toml.

Steam reads the app ID from steam_appid.txt in the process's working directory
(not next to steam_api64.dll), or from the SteamAppId environment variable.
SteamVirtualLAN is run from the repository root, so that is where it keeps
steam_appid.txt. Started from anywhere else it writes no file and sets
SteamAppId instead.
"""

import logging
import os
import sys
from pathlib import Path

from steamlan.steam import STEAM_API_DLL, SteamClient, SteamInitError

log = logging.getLogger(__name__)

# Valve's Spacewar test app, used while SteamVirtualLAN has no app ID of its own.
APP_ID = 480
APP_ID_FILE = "steam_appid.txt"


# The repository root, when running from a source checkout.
_SOURCE_ROOT = Path(__file__).resolve().parents[3]


def is_project_root(directory: Path) -> bool:
    return (directory / "pyproject.toml").is_file() and (directory / "src" / "steamlan").is_dir()


def steam_api_dir() -> Path:
    """The directory steam_api64.dll is loaded from.

    $STEAMLAN_STEAM_API_DIR if set; next to the executable when packaged;
    otherwise the repository root of the source checkout this code runs from,
    or else the working directory.
    """
    override = os.environ.get("STEAMLAN_STEAM_API_DIR")
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if is_project_root(_SOURCE_ROOT):
        return _SOURCE_ROOT
    return Path.cwd().resolve()


def ensure_steam_appid(directory: Path) -> Path:
    """Make directory/steam_appid.txt contain our app ID; returns its path."""
    path = directory / APP_ID_FILE
    try:
        current = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        current = None
    except (OSError, UnicodeDecodeError):
        current = ""
    if current is not None and current.strip() == str(APP_ID):
        return path

    if current is not None:
        log.warning("Replacing %s, which did not contain app ID %s", path, APP_ID)
    try:
        # Bytes, so that Windows doesn't turn the newline into CRLF.
        path.write_bytes(f"{APP_ID}\n".encode("ascii"))
    except OSError as exc:
        raise SteamInitError(f"Could not write {path}: {exc.strerror or exc}") from exc
    log.info("Wrote %s", path)
    return path


def prepare_app_id(working_dir: Path | None = None) -> Path | None:
    """Make sure Steam finds our app ID; returns the steam_appid.txt it relies on, if any."""
    working_dir = (working_dir or Path.cwd()).resolve()
    if is_project_root(working_dir):
        return ensure_steam_appid(working_dir)
    os.environ["SteamAppId"] = str(APP_ID)
    return None


def open_steam(directory: Path | None = None) -> SteamClient:
    """Start the Steam API with steam_api64.dll from steam_api_dir()."""
    prepare_app_id()
    steam = SteamClient((directory or steam_api_dir()) / STEAM_API_DLL)
    steam.start()
    return steam
