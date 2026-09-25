"""Setup shared by the developer scripts in this directory.

The scripts use steamworks/steam_api64.dll from the repository root. The app ID
is handled like in the app: steam_appid.txt in the repository root, written when
needed.
"""

import os
from pathlib import Path

from steamlan.app.steamworks import prepare_app_id
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
STEAM_ERRORS = (SteamAPILoadError, SteamInitError, SteamError)
POLL_INTERVAL = 0.05


def local_client(dll_path: str | os.PathLike[str] | None = None) -> SteamClient:
    prepare_app_id()
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
