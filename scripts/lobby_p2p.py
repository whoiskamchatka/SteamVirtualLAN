"""Connect lobby members to each other over SteamNetworkingSockets and exchange a hello.

Usage:
    python scripts/lobby_p2p.py host [FRIEND_STEAM_ID]
    python scripts/lobby_p2p.py guest [LOBBY_ID]

The host creates a friends-only lobby and, if given a SteamID, invites that
friend. The guest must be running before the invite is accepted in Steam chat:
Steam then hands the lobby to this process, which joins it. A LOBBY_ID printed
by the host can be passed instead of using an invite.

Once both are in the lobby they connect automatically, send a hello and an ack,
and keep running until Ctrl+C.
"""

import sys
import time
from collections.abc import Callable

from local_steam import POLL_INTERVAL, STEAM_ERRORS, local_client, parse_steam_id

from steamlan.steam import LobbyJoinRequest, LobbySession, SteamClient, decode_lobby_event

HELLO = b"steamvirtuallan hello"
HELLO_ACK = b"steamvirtuallan hello ack"
MAX_MEMBERS = 8
USAGE = "usage: python scripts/lobby_p2p.py host [FRIEND_STEAM_ID] | guest [LOBBY_ID]"


def hello_step(
    session: LobbySession, greeted: set[int], log: Callable[[str], None] = print
) -> None:
    """Poll once. The connecting side sends a hello, the other side answers it."""
    for steam_id, data in session.poll():
        if data == HELLO:
            log(f"Received hello from {steam_id}")
            session.send(steam_id, HELLO_ACK)
            log(f"Sent ack to {steam_id}")
            log(f"P2P test succeeded with {steam_id}")
        elif data == HELLO_ACK:
            log(f"Received ack from {steam_id}")
            log(f"P2P test succeeded with {steam_id}")
        else:
            log(f"Received {len(data)} bytes from {steam_id}")

    for steam_id in session.connected_peers():
        if session.peers[steam_id].initiator and steam_id not in greeted:
            session.send(steam_id, HELLO)
            greeted.add(steam_id)
            log(f"Sent hello to {steam_id}")


def wait_for_invite(steam: SteamClient) -> LobbyJoinRequest:
    while True:
        for callback in steam.run_callbacks():
            event = decode_lobby_event(callback)
            if isinstance(event, LobbyJoinRequest):
                return event
        time.sleep(POLL_INTERVAL)


def enter_lobby(steam: SteamClient, mode: str, target: int | None) -> int:
    if mode == "host":
        print("Creating lobby...")
        lobby_id = steam.create_lobby(MAX_MEMBERS)
        print(f"Lobby created: {lobby_id}")
        if target:
            steam.invite_to_lobby(lobby_id, target)
            print(f"Invite sent to {target}")
        return lobby_id

    if target is None:
        print("Waiting for a Steam lobby invite; accept it in Steam chat now...")
        request = wait_for_invite(steam)
        via = f" from {request.friend_id}" if request.friend_id else ""
        print(f"Invite accepted{via}")
        target = request.lobby_id
    print(f"Joining lobby {target}...")
    return steam.join_lobby(target)


def main() -> int:
    args = sys.argv[1:]
    try:
        if not args or args[0] not in ("host", "guest") or len(args) > 2:
            raise ValueError("expected host or guest")
        mode = args[0]
        target = parse_steam_id(args[1]) if len(args) == 2 else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    try:
        with local_client() as steam:
            print("Steam API initialized")
            print(f"Steam user: {steam.persona_name}")
            print(f"Steam ID: {steam.steam_id}")
            listen_socket = steam.create_listen_socket(0)
            lobby_id = None
            try:
                lobby_id = enter_lobby(steam, mode, target)
                session = LobbySession(steam, lobby_id, listen_socket, log=print)
                print("In lobby; waiting for peers (Ctrl+C to stop)...")
                try:
                    greeted: set[int] = set()
                    while True:
                        hello_step(session, greeted)
                        time.sleep(POLL_INTERVAL)
                finally:
                    session.close()
            finally:
                if lobby_id is not None:
                    print("Leaving lobby...")
                    steam.leave_lobby(lobby_id)
                steam.close_listen_socket(listen_socket)
    except KeyboardInterrupt:
        pass
    except STEAM_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Steam API shut down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
