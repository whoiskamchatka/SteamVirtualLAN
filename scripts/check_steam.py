"""Check the Steam binding against the real Steam API.

Initializes Steam, prints the current user, pumps callbacks, creates and leaves
a friends-only lobby and shuts down again.

Usage: python scripts/check_steam.py [PATH_TO_STEAM_API64_DLL]
"""

import sys
import time

from local_steam import STEAM_ERRORS, local_client

from steamlan.steam import SteamClient, SteamError


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

    try:
        with local_client(sys.argv[1] if len(sys.argv) == 2 else None) as steam:
            print("Steam API initialized")
            print(f"Steam user: {steam.persona_name}")
            print(f"Steam ID: {steam.steam_id}")

            received = 0
            for _ in range(10):
                received += len(steam.run_callbacks())
                time.sleep(0.05)
            print(f"Steam callbacks pumped ({received} received)")

            check_lobby(steam)
    except STEAM_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Steam API shut down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
