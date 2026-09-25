import os
from pathlib import Path

from steamlan.steam import STEAM_API_DLL, SteamClient, SteamInitError


def steamworks_dir() -> Path:
    """Where steam_api64.dll and steam_appid.txt live: $STEAMLAN_STEAMWORKS or ./steamworks."""
    return Path(os.environ.get("STEAMLAN_STEAMWORKS", "steamworks")).resolve()


def open_steam(directory: Path | None = None) -> SteamClient:
    """Start the Steam API with the DLL and app ID from the steamworks directory."""
    directory = directory or steamworks_dir()
    try:
        app_id = (directory / "steam_appid.txt").read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        app_id = ""
    if not app_id.isdecimal():
        raise SteamInitError(f"{directory / 'steam_appid.txt'} must contain the Steam app ID")

    # Steamworks reads the app ID from steam_appid.txt in the working directory
    # or from this environment variable.
    os.environ["SteamAppId"] = app_id
    steam = SteamClient(directory / STEAM_API_DLL)
    steam.start()
    return steam
