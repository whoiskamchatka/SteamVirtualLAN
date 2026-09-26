# SteamVirtualLAN

SteamVirtualLAN creates a virtual LAN between Steam friends using Steam P2P, so LAN games can work over the internet without manual port forwarding or VPN setup.

> **Alpha.** The virtual network works, but compatibility with real LAN games is still being tested.

## Features

- Create and join private virtual LAN networks
- Invite friends through the Steam overlay
- Virtual `10.77.0.x` address for every member
- Direct Steam P2P tunnel between members
- UDP broadcast and IPv4 multicast for LAN discovery
- Online/offline state, with your network restored automatically on the next start
- Automatic recovery when your connection drops
- Latency to each member
- Keeps running in the system tray
- No manual port forwarding
- Virtual adapter (Wintun) set up automatically

## Requirements

- Windows
- Steam, running and signed in
- 64-bit Python 3.11 or newer (for the current development version)
- Administrator permission (a UAC prompt) for the virtual network adapter
- `steam_api64.dll` from the [Steamworks SDK](https://partner.steamgames.com/) (`redistributable_bin/win64/`), placed in the repository root

The development version uses Valve's Spacewar test app ID (480), so Steam may show you as playing Spacewar.

## Quick start

```powershell
git clone https://github.com/whoiskamchatka/SteamVirtualLAN.git
cd SteamVirtualLAN
python -m venv .venv
.venv\Scripts\activate
pip install -e . --group dev
# copy steam_api64.dll into this folder, next to pyproject.toml
python -m steamlan
```

Then:

1. Click **Create Network**.
2. Your friend starts SteamVirtualLAN on their PC.
3. Click **Invite Steam Friend** and pick them in the Steam overlay.
4. Your friend accepts the invite in Steam.  
   *Alternatively, join with the Lobby ID and Access Code.*
5. Confirm the UAC prompt for the virtual adapter.
6. You're ready to connect over LAN.

If the invite doesn't work, your friend can use **Join Network** with the Lobby ID and access code shown on your network page.

Closing the window keeps SteamVirtualLAN running in the tray. Use **Go Offline** to pause, **Exit** in the tray menu to quit (you stay in the network), and **Leave Network** to leave for good.

## How it works

```text
PC A                                          PC B
10.77.0.1                                     10.77.0.2

Application                                   Application
  ↕                                             ↕
Windows                                       Windows
  ↕                                             ↕
Wintun virtual adapter                        Wintun virtual adapter
  ↕                  virtual LAN packets        ↕
SteamVirtualLAN  <─────── Steam P2P ───────>  SteamVirtualLAN
       │                                             │
       └──────────────── Steam lobby ────────────────┘
         membership, invites, addresses (control only)
```

**Two layers.** The Steam lobby is the control layer: it holds the network's membership, carries the invites, keeps track of which virtual address belongs to whom, and lets the members find each other. The actual virtual LAN traffic doesn't go through the lobby. Every pair of members has its own Steam P2P connection, and packets travel on it directly from one member to the other.

**A packet's journey:**

1. Every member gets its own virtual `10.77.0.x` address, which it keeps while it is a member.
2. Windows sees SteamVirtualLAN as a normal network adapter (a [Wintun](https://www.wintun.net) virtual adapter) on the `10.77.0.0/24` network.
3. When a LAN application sends a packet to another member's address, Windows routes it into that adapter.
4. SteamVirtualLAN reads the IPv4 packet and looks up which Steam friend owns the destination address.
5. The complete IP packet is sent unchanged over the Steam P2P connection to that member, including addresses, ports and data.
6. The receiving SteamVirtualLAN checks that the packet really comes from the sender's own virtual address and is addressed to this PC, then hands it to its own adapter.
7. Windows and the application on the other PC receive it like traffic from a local network.

**Who gets what:**

- **Unicast** (to one `10.77.0.x` address) goes only to that member.
- **Broadcast** (`10.77.0.255` and `255.255.255.255`) is copied once to every member you are connected to.
- **IPv4 multicast** (`224.0.0.0/4`) is copied the same way, since many games announce and discover LAN servers with broadcast or multicast.
- Broadcast and multicast received from another member are only handed to your own Windows, never sent on again, so they can't loop around the network.

Traffic never passes through a host or any other member: all members are equal, and each packet goes straight from sender to receiver. There is no router port forwarding to set up and no SteamVirtualLAN server in between. Steam's networking takes care of connecting the PCs, and may carry the connection over Valve's relay network when a direct path isn't possible.

## Current status

- The virtual tunnel works.
- Virtual networking between two clients has been tested.
- The broadcast/multicast path through the virtual adapter has been tested.
- Compatibility with real LAN games still needs testing.
- The project is in development (alpha).

## Development

```powershell
pytest
ruff check .
ruff format --check .
```

The tests don't need Steam, the Steamworks SDK or Administrator rights. The `scripts` folder has diagnostics that do, such as `python scripts/check_steam.py` and `python scripts/check_adapter.py --discovery`.

## Notes and limitations

- Windows only for now.
- At most 8 members online at the same time.
- A network exists only while at least one member is in its Steam lobby; once everyone is offline, it has to be created again.
- All members should run compatible SteamVirtualLAN versions.
- Compatibility with some LAN applications may vary while network discovery support is still being tested.

## Third-party components

- `steam_api64.dll` is Valve's and is not included in this repository; don't commit it.
- The virtual adapter uses [Wintun](https://www.wintun.net). The official `wintun.dll` is downloaded from wintun.net and verified on first use.

## Disclaimer

SteamVirtualLAN is an independent project and is not affiliated with or endorsed by Valve Corporation. Steam and Steamworks are trademarks of Valve Corporation.
