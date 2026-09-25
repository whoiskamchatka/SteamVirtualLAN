# SteamVirtualLAN

Create a virtual LAN with your Steam friends.

SteamVirtualLAN is an experimental project for connecting Steam friends to the same virtual network through Steam Networking, without sharing IP addresses or setting up port forwarding.

The project is still very early in development.

## Status

Nothing usable yet. SteamVirtualLAN can currently load `steam_api64.dll`, initialize the Steam API and read the current user's name and SteamID through a small internal Python binding.

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

The script initializes the Steam API, prints your Steam name and SteamID, and shuts it down again.

## Disclaimer

SteamVirtualLAN is an independent project and is not affiliated with or endorsed by Valve Corporation. Steam and Steamworks are trademarks of Valve Corporation.
