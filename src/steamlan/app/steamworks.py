"""Where SteamVirtualLAN finds Steam: the local Steam DLL and the app ID.

Steam reads the app ID from steam_appid.txt in the process's working directory
(not next to steam_api64.dll), or from the SteamAppId environment variable.
SteamVirtualLAN is run from the repository root, so that is where it keeps
steam_appid.txt. Started from anywhere else it writes no file and sets
SteamAppId instead.
"""

import logging
import os
from pathlib import Path

from steamlan.steam import STEAM_API_DLL, SteamClient, SteamInitError

log = logging.getLogger(__name__)

# Valve's Spacewar test app, used while SteamVirtualLAN has no app ID of its own.
APP_ID = 480
APP_ID_FILE = "steam_appid.txt"


def steamworks_dir() -> Path:
    """Where steam_api64.dll lives: $STEAMLAN_STEAMWORKS or ./steamworks."""
    return Path(os.environ.get("STEAMLAN_STEAMWORKS", "steamworks")).resolve()


def is_project_root(directory: Path) -> bool:
    return (directory / "pyproject.toml").is_file() and (directory / "src" / "steamlan").is_dir()


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
    """Start the Steam API with the DLL from the steamworks directory."""
    prepare_app_id()
    steam = SteamClient((directory or steamworks_dir()) / STEAM_API_DLL)
    steam.start()
    return steam
