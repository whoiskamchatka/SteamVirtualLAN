"""Wait for an accepted Steam lobby invite, join the lobby and show its members.

Usage: python scripts/join_lobby.py

Keep this running, then accept the invite in Steam. Stop with Ctrl+C.
"""

import sys
import time

from local_steam import (
    POLL_INTERVAL,
    STEAM_ERRORS,
    local_client,
    print_members,
    watch_members,
)

from steamlan.steam import LobbyJoinRequest, SteamClient, decode_lobby_event


def wait_for_join_request(steam: SteamClient) -> LobbyJoinRequest:
    while True:
        for callback in steam.run_callbacks():
            event = decode_lobby_event(callback)
            if isinstance(event, LobbyJoinRequest):
                return event
        time.sleep(POLL_INTERVAL)


def main() -> int:
    if len(sys.argv) > 1:
        print("usage: python scripts/join_lobby.py", file=sys.stderr)
        return 2

    try:
        with local_client() as steam:
            print(f"Steam user: {steam.persona_name}")
            print("Waiting for Steam lobby invite (Ctrl+C to stop)...")
            request = wait_for_join_request(steam)
            via = f" via {request.friend_id}" if request.friend_id else ""
            print(f"Invite accepted{via}")

            print("Joining lobby...")
            lobby_id = request.lobby_id
            try:
                steam.join_lobby(lobby_id)
                print(f"Joined lobby: {lobby_id}")
                print_members(steam, lobby_id)
                print("In lobby (Ctrl+C to leave)...")
                watch_members(steam, lobby_id)
            finally:
                # Also covers a join that completes after a timeout or Ctrl+C.
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
