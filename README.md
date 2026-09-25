# SteamLAN

Create a virtual LAN with your Steam friends.

SteamLAN is an experimental project for connecting Steam friends to the same
virtual network without exchanging IP addresses or setting up port forwarding.

The project is still very early in development.

## Status

Nothing usable yet. So far there is only a loader for `steam_api64.dll`, which
will be the base for a small internal Steamworks binding. Windows is the
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

The Steamworks SDK is not included in this repository. To load the real
library, download the SDK from Valve and point SteamLAN at
`redistributable_bin/win64/steam_api64.dll`.

## Disclaimer

SteamLAN is an independent project and is not affiliated with or endorsed by
Valve Corporation. Steam and Steamworks are trademarks of Valve Corporation.
