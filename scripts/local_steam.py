"""Setup shared by the developer scripts in this directory.

The scripts use steamworks/steam_api64.dll and steamworks/steam_appid.txt from
the repository root.
"""

import os
from pathlib import Path

from steamlan.steam import (
    STEAM_API_DLL,
    ConnectionStatusChange,
    SteamAPILoadError,
    SteamClient,
    SteamError,
    SteamInitError,
    decode_networking_event,
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


def parse_steam_id(text: str) -> int:
    if not text.isdecimal() or not 0 < int(text) < 2**64:
        raise ValueError(f"not a SteamID: {text!r}")
    return int(text)


def connection_events(steam: SteamClient) -> list[ConnectionStatusChange]:
    events = (decode_networking_event(callback) for callback in steam.run_callbacks())
    return [event for event in events if isinstance(event, ConnectionStatusChange)]


def describe_end(event: ConnectionStatusChange) -> str:
    return f"{event.state.name}, reason {event.end_reason}: {event.end_debug or 'no details'}"
