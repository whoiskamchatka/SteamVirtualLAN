# SteamVirtualLAN

Create a virtual LAN with your Steam friends.

SteamVirtualLAN is an experimental project for connecting Steam friends to the same virtual network through Steam Networking, without sharing IP addresses or setting up port forwarding.

The project is still very early in development.

## Status

Nothing usable yet. Through a small internal Python binding, SteamVirtualLAN can currently initialize the Steam API, read the current user's name and SteamID, create a Steam lobby, invite a Steam friend to it and see members enter and leave. Sending data between two Steam accounts over SteamNetworkingSockets works; connecting lobby members to each other automatically is being tested.

Windows is the initial target.

## Development

You need 64-bit Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e . --group dev
```

Run the tests and linter:

```powershell
pytest
ruff check .
ruff format --check .
```

The tests don't require Steam or the Steamworks SDK.

## Desktop app

With Steam running and the `steamworks` directory set up as described below, start the app from the repository root:

```powershell
python -m steamlan
```

Create Lobby starts a network and shows its Lobby ID and access code. Invite Steam Friend opens the Steam overlay, where you pick friends to invite; the invite carries the lobby and access code, and the friend accepts it in Steam while the app is open on their PC. Anyone else can join with Join Lobby, using the Lobby ID and access code. The access code only exists in the host's app, in the invites it sends and with the people it is given to, and the host checks it over the Steam connection; it is never stored in the lobby.

The app connects members to each other but doesn't carry any game traffic yet.

## Testing with the real Steam API

Steamworks files are not included in this repository. For local development, put them in a `steamworks` directory at the repository root:

```text
steamworks/
    steam_api64.dll
    steam_appid.txt
```

`steam_api64.dll` can be found in the Steamworks SDK under `redistributable_bin/win64/`.

For development, `steam_appid.txt` should contain `480`, Valve's Spacewar test App ID.

With Steam running and logged in:

```powershell
python scripts/check_steam.py
```

The script initializes the Steam API, prints your Steam name and SteamID, creates a friends-only lobby, checks that you are a member, leaves it and shuts down again.

`python scripts/check_networking.py` checks that SteamNetworkingSockets is available and that its local identity is your SteamID. It doesn't open any connections.

To test lobby invites you need a second Steam account that is your friend, signed in on another PC. Run `python scripts/host_lobby.py <SteamID>` with the friend's SteamID. The friend accepts the invitation from Steam chat and Steam joins them to the lobby; the script prints members as they enter and leave. Stop it with Ctrl+C.

To test a direct SteamNetworkingSockets connection between two accounts, run `python scripts/p2p_host.py` on one PC and `python scripts/p2p_guest.py <SteamID>` on the other, using the Steam ID the host prints. The two exchange a short hello message and exit.

To let the lobby connect peers instead, start `python scripts/lobby_p2p.py guest` on the joining PC first, then run `python scripts/lobby_p2p.py host <SteamID>` on the other PC with the joining account's SteamID and accept the invite in Steam chat on the joining PC. Both sides connect on their own and exchange a hello. If the invite doesn't reach the guest script, pass the lobby ID the host prints instead: `python scripts/lobby_p2p.py guest <lobby ID>`.

## Disclaimer

SteamVirtualLAN is an independent project and is not affiliated with or endorsed by Valve Corporation. Steam and Steamworks are trademarks of Valve Corporation.
