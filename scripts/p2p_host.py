"""Accept one SteamNetworkingSockets P2P connection and exchange a hello over it.

Usage: python scripts/p2p_host.py

Then run scripts/p2p_guest.py with the SteamID this prints on another PC that
is signed in to a different Steam account. Stop with Ctrl+C.
"""

import sys
import time

from local_steam import (
    POLL_INTERVAL,
    STEAM_ERRORS,
    connection_events,
    describe_end,
    local_client,
)

from steamlan.steam import ConnectionState, SteamClient, SteamError

HOST_HELLO = b"hello from host"
GUEST_HELLO = b"hello from guest"
TIMEOUT = 60.0
# Steam sends from inside this process, so keep it running briefly after
# closing for the close to reach the guest.
CLOSE_GRACE = 1.0


def exchange(steam: SteamClient, connections: list[int]) -> bytes:
    """Accept the first peer, send our hello once connected and return its reply."""
    deadline = time.monotonic() + TIMEOUT
    connection = None
    connected = False
    while time.monotonic() < deadline:
        for event in connection_events(steam):
            if event.incoming:
                connections.append(event.connection)
                if connection is not None:
                    steam.close_connection(event.connection, "already connected")
                    continue
                print(f"Incoming connection from {event.remote_steam_id}")
                steam.accept_connection(event.connection)
                connection = event.connection
                print("Connection accepted")
            elif event.connection == connection:
                if event.state is ConnectionState.CONNECTED and not connected:
                    connected = True
                    print("Connected")
                    steam.send_message(connection, HOST_HELLO)
                    print(f"Sent: {HOST_HELLO.decode()}")
                elif event.ended:
                    raise SteamError(f"connection ended ({describe_end(event)})")

        if connected:
            for message in steam.receive_messages(connection):
                return message
        time.sleep(POLL_INTERVAL)

    raise SteamError(f"no reply from a guest within {TIMEOUT:g}s")


def main() -> int:
    if len(sys.argv) > 1:
        print("usage: python scripts/p2p_host.py", file=sys.stderr)
        return 2

    try:
        with local_client() as steam:
            print("Steam API initialized")
            print(f"Steam user: {steam.persona_name}")
            print(f"Steam ID: {steam.steam_id}")

            listen_socket = steam.create_listen_socket(0)
            connections: list[int] = []
            try:
                print("P2P listen socket created")
                print("Waiting for connection...")
                reply = exchange(steam, connections)
                print(f"Received: {reply.decode(errors='replace')}")
                if reply != GUEST_HELLO:
                    raise SteamError(f"unexpected reply {reply!r}")
                print("P2P test succeeded")
            finally:
                for connection in connections:
                    steam.close_connection(connection)
                steam.close_listen_socket(listen_socket)

            end = time.monotonic() + CLOSE_GRACE
            while time.monotonic() < end:
                steam.run_callbacks()
                time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 1
    except STEAM_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Steam API shut down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
