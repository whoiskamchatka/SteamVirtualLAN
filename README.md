# SteamLAN

Create a virtual LAN with your Steam friends.

SteamLAN is an experimental project for connecting Steam friends to the same
virtual network without exchanging IP addresses or setting up port forwarding.

The project is still very early in development.

## Status

Nothing usable yet. So far SteamLAN can load `steam_api64.dll` and initialize
and shut down the Steam API through a small internal binding. Windows is the
initial target.

## Development

You need 64-bit Python 3.11 or newer.

```
python -m venv .venv
.venv\Scripts\activate
pip install -e . --group dev
```

Run the tests and linter:

```
pytest
ruff check .
ruff format --check .
```

The tests don't need Steam or the Steamworks SDK.

## Testing with the real Steam API

The Steamworks files come from your own local setup and are not tracked by
git. Put them in a `steamworks` directory at the repository root:

```
steamworks/
    steam_api64.dll
    steam_appid.txt
```

`steam_api64.dll` is in the Steamworks SDK under
`redistributable_bin/win64/`. `steam_appid.txt` should contain `480`, the app
ID of Valve's Spacewar test app.

With Steam running and logged in:

```
python scripts/check_steam.py
```

It initializes the Steam API, prints a message and shuts it down again. If
initialization fails, Steamworks prints the reason just before the error.

## Disclaimer

SteamLAN is an independent project and is not affiliated with or endorsed by
Valve Corporation. Steam and Steamworks are trademarks of Valve Corporation.
