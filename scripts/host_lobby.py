"""Create a friends-only lobby, invite a friend and show members joining and leaving.

Usage: python scripts/host_lobby.py FRIEND_STEAM_ID

The friend accepts the invite from Steam chat and Steam joins them to the
lobby. Stop with Ctrl+C.
"""

import sys
import time

from local_steam import STEAM_ERRORS, local_client

from steamlan.steam import (
    ChatMemberStateChange,
    LobbyMemberUpdate,
    SteamClient,
    decode_lobby_event,
)

MAX_MEMBERS = 8
POLL_INTERVAL = 0.05


def print_members(steam: SteamClient, lobby_id: int) -> None:
    members = steam.lobby_members(lobby_id)
    print(f"Members: {len(members)}")
    for member in members:
        print(f"  {member}")


def watch_members(steam: SteamClient, lobby_id: int) -> None:
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


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("usage: python scripts/host_lobby.py FRIEND_STEAM_ID", file=sys.stderr)
        return 2
    friend_id = int(sys.argv[1])

    try:
        with local_client() as steam:
            print(f"Steam user: {steam.persona_name}")
            print("Creating lobby...")
            lobby_id = steam.create_lobby(MAX_MEMBERS)
            try:
                print(f"Lobby created: {lobby_id}")
                print_members(steam, lobby_id)

                print(f"Inviting {friend_id}...")
                steam.invite_to_lobby(lobby_id, friend_id)
                print("Invite sent.")
                print("Ask your friend to accept/join from the Steam chat invitation.")

                print("Waiting for members (Ctrl+C to stop)...")
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
