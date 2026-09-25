"""Check the Steam binding against the real Steam API.

Initializes Steam, prints the current user, pumps callbacks, creates and leaves
a friends-only lobby and shuts down again.

Uses steamworks/steam_api64.dll and steamworks/steam_appid.txt from the
repository root unless a DLL path is given.
"""

import os
import sys
import time
from pathlib import Path

from steamlan.steam import (
    STEAM_API_DLL,
    SteamAPILoadError,
    SteamClient,
    SteamError,
    SteamInitError,
)

STEAMWORKS_DIR = Path(__file__).resolve().parent.parent / "steamworks"
APP_ID_FILE = STEAMWORKS_DIR / "steam_appid.txt"


def read_app_id() -> str:
    try:
        app_id = APP_ID_FILE.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        raise SystemExit(
            "error: steamworks/steam_appid.txt is required for the smoke test "
            "(it should contain 480)"
        ) from None
    except UnicodeDecodeError:
        app_id = ""

    if not app_id.isdigit():
        raise SystemExit(
            "error: steamworks/steam_appid.txt should contain only an app ID, e.g. 480"
        )
    return app_id


def check_lobby(steam: SteamClient) -> None:
    print("Creating lobby...")
    lobby_id = steam.create_lobby(max_members=4)
    try:
        print(f"Lobby created: {lobby_id}")
        print(f"Members: {steam.lobby_member_count(lobby_id)}")
        if steam.steam_id not in steam.lobby_members(lobby_id):
            raise SteamError("the current user is not a member of the new lobby")
    finally:
        print("Leaving lobby...")
        steam.leave_lobby(lobby_id)


def main() -> int:
    if len(sys.argv) > 2:
        print("usage: python scripts/check_steam.py [PATH_TO_STEAM_API64_DLL]", file=sys.stderr)
        return 2

    dll_path = sys.argv[1] if len(sys.argv) == 2 else STEAMWORKS_DIR / STEAM_API_DLL

    # Steamworks looks for steam_appid.txt only in the working directory, but it
    # also accepts the app ID from the SteamAppId environment variable, so the
    # script works regardless of where it is run from.
    os.environ["SteamAppId"] = read_app_id()

    try:
        with SteamClient(dll_path) as steam:
            print("Steam API initialized")
            print(f"Steam user: {steam.persona_name}")
            print(f"Steam ID: {steam.steam_id}")

            received = 0
            for _ in range(10):
                received += len(steam.run_callbacks())
                time.sleep(0.05)
            print(f"Steam callbacks pumped ({received} received)")

            check_lobby(steam)
    except (SteamAPILoadError, SteamInitError, SteamError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Steam API shut down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
