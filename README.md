# SteamVirtualLAN

Create a virtual LAN with your Steam friends.

SteamVirtualLAN is an experimental project for connecting Steam friends to the same virtual network through Steam Networking, without sharing IP addresses or setting up port forwarding.

The project is still very early in development.

## Status

Nothing usable yet. Through a small internal Python binding, SteamVirtualLAN can currently initialize the Steam API, read the current user's name and SteamID, pump Steam callbacks, and create and leave a Steam lobby. Inviting a friend and joining their lobby is implemented but has not been tested between two Steam accounts yet.

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

To test lobby invites you need two Steam accounts that are friends, on two PCs with the same setup. Start `python scripts/join_lobby.py` on one, then `python scripts/host_lobby.py <SteamID>` on the other with the joining account's SteamID, and accept the invite in Steam on the joining PC. The Steam overlay can't show its invite dialog for these console scripts, so the host sends the invite directly. Stop either script with Ctrl+C.

## Disclaimer

SteamVirtualLAN is an independent project and is not affiliated with or endorsed by Valve Corporation. Steam and Steamworks are trademarks of Valve Corporation.
