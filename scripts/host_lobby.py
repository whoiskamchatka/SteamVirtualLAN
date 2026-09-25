"""Create a friends-only lobby and show friends joining and leaving it.

Usage: python scripts/host_lobby.py [FRIEND_STEAM_ID]

Asks Steam to open its invite dialog for the lobby. The overlay can only show
it in a process it has hooked, so a friend's SteamID can also be given to send
them an invite directly. Stop with Ctrl+C.
"""

import sys

from local_steam import STEAM_ERRORS, local_client, print_members, watch_members

MAX_MEMBERS = 8


def main() -> int:
    args = sys.argv[1:]
    if len(args) > 1 or (args and not args[0].isdigit()):
        print("usage: python scripts/host_lobby.py [FRIEND_STEAM_ID]", file=sys.stderr)
        return 2
    friend_id = int(args[0]) if args else None

    try:
        with local_client() as steam:
            print(f"Steam user: {steam.persona_name}")
            print("Creating lobby...")
            lobby_id = steam.create_lobby(MAX_MEMBERS)
            try:
                print(f"Lobby created: {lobby_id}")
                print_members(steam, lobby_id)

                print("Opening Steam invite dialog...")
                steam.open_lobby_invite(lobby_id)
                if friend_id:
                    print(f"Inviting {friend_id}...")
                    steam.invite_to_lobby(lobby_id, friend_id)

                print("Waiting for players (Ctrl+C to stop)...")
                watch_members(steam, lobby_id)
            finally:
                print("Leaving lobby...")
                steam.leave_lobby(lobby_id)
    except KeyboardInterrupt:
        pass
    except STEAM_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Steam API shut down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
