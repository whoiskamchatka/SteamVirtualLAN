# SteamVirtualLAN

Create a virtual LAN with your Steam friends.

SteamVirtualLAN is an experimental project for connecting Steam friends to the same virtual network through Steam Networking, without sharing IP addresses or setting up port forwarding.

The project is still very early in development.

## Status

Nothing usable yet. The desktop app creates and joins Steam lobbies, connects the members to each other over Steam, gives every member a virtual IP address (10.77.0.x) on its own virtual network adapter and carries IPv4 packets between them. Carrying packets between two PCs has not been tested yet; broadcast and multicast (which many games use to find LAN servers) are not carried.

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

With Steam running and `steamworks/steam_api64.dll` in place as described below, start the app from the repository root:

```powershell
python -m steamlan
```

Create Lobby starts a network and shows its Lobby ID and access code. Invite Steam Friend opens the Steam overlay, where you pick friends to invite; the invite carries the lobby and access code, and the friend accepts it in Steam while the app is open on their PC. Anyone else can join with Join Lobby, using the Lobby ID and access code. The access code only exists in the host's app, in the invites it sends and with the people it is given to, and the host checks it over the Steam connection; it is never stored in the lobby.

Creating or joining a network also brings up SteamVirtualLAN's virtual network adapter (see below). Windows asks for Administrator permission for it with its UAC prompt; the app itself, with Steam and the window, keeps running without Administrator rights, and only a small helper process started through the prompt owns the adapter. If the permission is not given, the app leaves the network again and says why.

The host is always 10.77.0.1. The host gives every member it admits the next free address, 10.77.0.2, 10.77.0.3 and so on, and sends all members the same list of which Steam account has which address; members never choose their own. The member list shows each member's address.

Packets Windows sends to another member's address go into the adapter, through the helper to the app, and over the Steam connection to that member, whose app hands them to its own adapter and so to Windows. A member only accepts packets from the address the host gave the member that sent them, and only for its own address. Only IPv4 packets between two members' addresses are carried.

Leave Lobby or closing the window first removes the adapter and ends the helper, then leaves the lobby and closes the Steam connections.

## Testing with the real Steam API

Valve's `steam_api64.dll` is not included in this repository and must not be committed. Copy it from the Steamworks SDK (`redistributable_bin/win64/`) into a `steamworks` directory at the repository root; git ignores that directory:

```text
steamworks/
    steam_api64.dll
```

Steam reads the app ID from `steam_appid.txt` in the working directory. The app and the scripts write that file (`480`, Valve's Spacewar test app) into the repository root when it is missing or wrong, so always start them from there. Started from anywhere else they pass the app ID in the `SteamAppId` environment variable instead.

With Steam running and logged in:

```powershell
python scripts/check_steam.py
```

The script initializes the Steam API, prints your Steam name and SteamID, creates a friends-only lobby, checks that you are a member, leaves it and shuts down again.

`python scripts/check_networking.py` checks that SteamNetworkingSockets is available and that its local identity is your SteamID. It doesn't open any connections.

To test lobby invites you need a second Steam account that is your friend, signed in on another PC. Run `python scripts/host_lobby.py <SteamID>` with the friend's SteamID. The friend accepts the invitation from Steam chat and Steam joins them to the lobby; the script prints members as they enter and leave. Stop it with Ctrl+C.

To test a direct SteamNetworkingSockets connection between two accounts, run `python scripts/p2p_host.py` on one PC and `python scripts/p2p_guest.py <SteamID>` on the other, using the Steam ID the host prints. The two exchange a short hello message and exit.

To let the lobby connect peers instead, start `python scripts/lobby_p2p.py guest` on the joining PC first, then run `python scripts/lobby_p2p.py host <SteamID>` on the other PC with the joining account's SteamID and accept the invite in Steam chat on the joining PC. Both sides connect on their own and exchange a hello. If the invite doesn't reach the guest script, pass the lobby ID the host prints instead: `python scripts/lobby_p2p.py guest <lobby ID>`.

## Virtual network adapter

SteamVirtualLAN's virtual adapter uses [Wintun](https://www.wintun.net)'s signed `wintun.dll`, which is meant to be shipped next to the application and installs its driver by itself; there is no separate installer. The desktop app sets it up by itself. There is also a diagnostic that brings the adapter up on its own, without Steam; don't run it while the app has a network open, as both use the same adapter.

From the repository root:

```powershell
python scripts/check_adapter.py --reply
```

The first time, the script (like the app) downloads the official Wintun 0.14.1 package from wintun.net, checks it against its published SHA-256 and keeps only the `wintun.dll` for your CPU in the ignored `wintun` directory; nothing unverified is ever loaded. The adapter needs Administrator rights, so the script then asks for them through Windows' UAC prompt and continues in a new window. There it creates an adapter named SteamVirtualLAN with the address 10.77.0.1/24 (no gateway or DNS; no other adapter or setting is changed) and prints each packet Windows sends into it. In a second terminal run:

```powershell
ping 10.77.0.2
```

The diagnostic should print `IPv4 ICMP 10.77.0.1 -> 10.77.0.2` for every ping. With `--reply` it also answers them itself, so ping should show replies from 10.77.0.2 (the app never does this; there, replies come from the other PC). Stop it with Ctrl+C; the adapter is removed when it stops. `--remove-driver` also uninstalls Wintun's driver afterwards if nothing else uses it.

## Disclaimer

SteamVirtualLAN is an independent project and is not affiliated with or endorsed by Valve Corporation. Steam and Steamworks are trademarks of Valve Corporation.
