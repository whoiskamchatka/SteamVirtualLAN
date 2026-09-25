"""Check that SteamNetworkingSockets is available and knows the current user.

Initializes Steam, reads the local networking identity, compares it with the
logged-in Steam user and shuts down again. Nothing is sent over the network.

Usage: python scripts/check_networking.py
"""

import sys

from local_steam import STEAM_ERRORS, local_client

from steamlan.steam import SteamError


def main() -> int:
    if len(sys.argv) > 1:
        print("usage: python scripts/check_networking.py", file=sys.stderr)
        return 2

    try:
        with local_client() as steam:
            print("Steam API initialized")
            print(f"Steam user: {steam.persona_name}")
            print(f"Steam ID: {steam.steam_id}")

            networking_id = steam.networking_steam_id
            print("SteamNetworkingSockets available")
            print(f"Networking identity: SteamID {networking_id}")
            if networking_id != steam.steam_id:
                raise SteamError("networking identity does not match the current Steam user")
            print("Identity matches current Steam user")
    except STEAM_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Steam API shut down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
