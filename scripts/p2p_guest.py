"""Connect to scripts/p2p_host.py over SteamNetworkingSockets and exchange a hello.

Usage: python scripts/p2p_guest.py HOST_STEAM_ID

HOST_STEAM_ID is the "Steam ID" line printed by the host. The host must be
signed in to a different Steam account on another PC. Stop with Ctrl+C.
"""

import sys
import time

from local_steam import (
    POLL_INTERVAL,
    STEAM_ERRORS,
    connection_events,
    describe_end,
    local_client,
    parse_steam_id,
)

from steamlan.steam import ConnectionState, SteamClient, SteamError

HOST_HELLO = b"hello from host"
GUEST_HELLO = b"hello from guest"
TIMEOUT = 60.0
CLOSE_TIMEOUT = 15.0


def receive_hello(steam: SteamClient, connection: int) -> bytes:
    """Wait until connected and return the first message from the host."""
    deadline = time.monotonic() + TIMEOUT
    connected = False
    while time.monotonic() < deadline:
        for event in connection_events(steam):
            if event.connection != connection:
                continue
            if event.state is ConnectionState.CONNECTED and not connected:
                connected = True
                print("Connected")
            elif event.ended:
                raise SteamError(f"connection ended ({describe_end(event)})")

        if connected:
            for message in steam.receive_messages(connection):
                return message
        time.sleep(POLL_INTERVAL)

    state = "no message from the host" if connected else "could not connect"
    raise SteamError(f"{state} within {TIMEOUT:g}s")


def wait_for_host_close(steam: SteamClient, connection: int) -> None:
    # The host closes the connection once it has our reply. Closing first could
    # drop the reply before Steam has delivered it.
    deadline = time.monotonic() + CLOSE_TIMEOUT
    while time.monotonic() < deadline:
        for event in connection_events(steam):
            if event.connection == connection and event.ended:
                if event.state is not ConnectionState.CLOSED_BY_PEER:
                    raise SteamError(f"connection ended ({describe_end(event)})")
                return
        time.sleep(POLL_INTERVAL)
    raise SteamError(f"host did not confirm the reply within {CLOSE_TIMEOUT:g}s")


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("expected one HOST_STEAM_ID")
        host_id = parse_steam_id(sys.argv[1])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("usage: python scripts/p2p_guest.py HOST_STEAM_ID", file=sys.stderr)
        return 2

    try:
        with local_client() as steam:
            print("Steam API initialized")
            print(f"Steam user: {steam.persona_name}")
            print(f"Steam ID: {steam.steam_id}")
            if host_id == steam.steam_id:
                raise SteamError("HOST_STEAM_ID is this account; run the host on another account")

            print(f"Connecting to {host_id}...")
            connection = steam.connect_p2p(host_id, 0)
            try:
                hello = receive_hello(steam, connection)
                print(f"Received: {hello.decode(errors='replace')}")
                if hello != HOST_HELLO:
                    raise SteamError(f"unexpected message {hello!r}")
                steam.send_message(connection, GUEST_HELLO)
                print(f"Sent: {GUEST_HELLO.decode()}")
                wait_for_host_close(steam, connection)
                print("P2P test succeeded")
            finally:
                steam.close_connection(connection)
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
