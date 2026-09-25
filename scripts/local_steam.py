"""Setup shared by the developer scripts in this directory.

The scripts use steamworks/steam_api64.dll and steamworks/steam_appid.txt from
the repository root.
"""

import os
import time
from pathlib import Path

from steamlan.steam import (
    STEAM_API_DLL,
    ChatMemberStateChange,
    LobbyMemberUpdate,
    SteamAPILoadError,
    SteamClient,
    SteamError,
    SteamInitError,
    decode_lobby_event,
)

STEAMWORKS_DIR = Path(__file__).resolve().parent.parent / "steamworks"
APP_ID_FILE = STEAMWORKS_DIR / "steam_appid.txt"
STEAM_ERRORS = (SteamAPILoadError, SteamInitError, SteamError)
POLL_INTERVAL = 0.05


def read_app_id() -> str:
    try:
        app_id = APP_ID_FILE.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        raise SystemExit(
            "error: steamworks/steam_appid.txt is required for the developer scripts "
            "(it should contain 480)"
        ) from None
    except UnicodeDecodeError:
        app_id = ""

    if not app_id.isdigit():
        raise SystemExit(
            "error: steamworks/steam_appid.txt should contain only an app ID, e.g. 480"
        )
    return app_id


def local_client(dll_path: str | os.PathLike[str] | None = None) -> SteamClient:
    # Steamworks looks for steam_appid.txt only in the working directory, but it
    # also accepts the app ID from the SteamAppId environment variable, so the
    # scripts work regardless of where they are run from.
    os.environ["SteamAppId"] = read_app_id()
    return SteamClient(dll_path or STEAMWORKS_DIR / STEAM_API_DLL)


def print_members(steam: SteamClient, lobby_id: int) -> None:
    members = steam.lobby_members(lobby_id)
    print(f"Members: {len(members)}")
    for member in members:
        print(f"  {member}")


def watch_members(steam: SteamClient, lobby_id: int) -> None:
    """Print lobby member changes until interrupted."""
    while True:
        for callback in steam.run_callbacks():
            event = decode_lobby_event(callback)
            if not isinstance(event, LobbyMemberUpdate) or event.lobby_id != lobby_id:
                continue
            if ChatMemberStateChange.ENTERED in event.state:
                print(f"Member joined: {event.user_id}")
            else:
                print(f"Member left: {event.user_id} ({event.state.name})")
            print_members(steam, lobby_id)
        time.sleep(POLL_INTERVAL)
