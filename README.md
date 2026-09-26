# SteamVirtualLAN

Create a virtual LAN with your Steam friends.

SteamVirtualLAN is an experimental project for connecting Steam friends to the same virtual network through Steam Networking, without sharing IP addresses or setting up port forwarding.

The project is still very early in development.

## Status

Nothing usable yet. The desktop app creates and joins networks of Steam friends, connects the members to each other over Steam, gives every member a virtual IP address (10.77.0.x) on its own virtual network adapter and carries IPv4 packets directly between them. It keeps running in the notification area, remembers its network between runs and doesn't depend on the member that created the network. Carrying packets between two PCs has not been tested yet; broadcast and multicast (which many games use to find LAN servers) are not carried.

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

With Steam running and `steam_api64.dll` in place as described below, start the app from the repository root:

```powershell
python -m steamlan
```

Create Network starts a network and shows its Lobby ID and access code. Invite Steam Friend opens the Steam overlay, where you pick friends to invite; the invite carries the lobby and access code, and the friend accepts it in Steam while the app is open on their PC. Anyone else can join with Join Network, using the Lobby ID and access code. Every member of a network knows its access code and can invite others. The code only exists in the members' apps, their saved network state (see below) and the invites they send, and is checked over the encrypted Steam connection; it is never stored in the lobby.

All members of a network are equal. The member list shows everyone's name, virtual IP address and whether they are Online or Offline; only your own row is marked, with "You".

Going online in a network also brings up SteamVirtualLAN's virtual network adapter (see below). Windows asks for Administrator permission for it with its UAC prompt; the app itself, with Steam and the window, keeps running without Administrator rights, and only a small helper process started through the prompt owns the adapter. If the permission is not given, the app goes offline again and says why.

Every member gets its own address, which it keeps until it leaves the network for good: the member that creates a network is 10.77.0.1, and the others get the lowest free address when they are first admitted. Packets Windows sends to another member's address go into the adapter, through the helper to the app, and over a direct Steam connection to that member, whose app hands them to its own adapter and so to Windows. They never pass through a third member. A member only accepts packets from the address that belongs to the member that sent them, and only for its own address. Only IPv4 packets between two members' addresses are carried.

### Closing, going offline, exiting and leaving

- **Closing the window** (X) only hides it. SteamVirtualLAN keeps running in the notification area and stays online: Steam, the lobby, the connections to the other members and the virtual adapter all keep going. Its tray icon's menu has Open SteamVirtualLAN, the current status, Go Offline or Go Online, Leave Network and Exit. Starting the app again while it runs only brings its window back.
- **Go Offline** makes this PC unavailable in the network: the adapter is removed and the app leaves the Steam lobby and closes its connections, but you stay a member and keep your address. It stays offline, also across restarts, until you choose Go Online. Online and Offline are SteamVirtualLAN's own, unrelated to your status in Steam's friends list.
- **Exit** (in the tray menu) stops the app: the adapter, the connections and Steam. It does not leave the network. The next time the app starts it goes back online in the same network, with the same address, by itself.
- **Leave Network** leaves for good. The other members are told, so that your address is freed, and the app forgets the network; to come back you need an invite or the access code again. Leaving while offline goes online briefly, without the adapter, only to tell the network.

### Saved network state

The network you are in is saved in `%LOCALAPPDATA%\SteamVirtualLAN\state.json`, per Steam account: the lobby, a random ID that tells the network apart from any other lobby, the access code, the members and their addresses, and whether you were online. The file is versioned JSON; a file SteamVirtualLAN can't read or that has another version is set aside as `state.json.bak` instead of being overwritten. `STEAMLAN_STATE_DIR` puts it somewhere else.

When the app starts, or you choose Go Online, it joins the saved lobby again. If the network still has you, you get your old address back without the access code. If Steam says the lobby no longer exists, or the lobby is now something else, the app says so and forgets the network.

### How a network works without the member that created it

A network is a Steam lobby. Some decisions need exactly one member to make them: admitting new members after checking their access code, giving them addresses, and taking members out when they leave for good. That member is always the one Steam considers the lobby's owner. It is an internal duty, not a role anyone sees, and nothing else depends on it; in particular, packets never go through it.

Steamworks (checked against SDK 1.65, `ISteamMatchmaking` `SteamMatchMaking009`) guarantees that a lobby has exactly one owner among the members currently in it. When the owner leaves the lobby or loses its connection to Steam, Steam makes another member the owner by itself (`GetLobbyOwner`); which one isn't documented, and Steam never hands ownership back to someone who returns. Only the owner can change lobby metadata (`SetLobbyData`), which is where the list of members and their addresses is kept, including members who are offline. So when the member that created a network goes offline, the others go on talking to each other, a new owner carries on admitting members with the same list, and when the creator comes back it is an ordinary member with its old address. The rare case of Steam making a member the owner before it has been admitted is handled too: that member passes ownership on (`SetLobbyOwner`) to an online member of the network, and nobody accepts a member list from an owner they don't already trust.

### What Steam lobbies can't do

A Steam lobby only exists while somebody is in it: Steam destroys it when its last member leaves ("Once all the users have left, the lobby is automatically destroyed on the back-end"), and it can't be joined or recreated with the same ID afterwards. Steam's only lobbies that survive being empty (`k_ELobbyTypePrivateUnique`) can only be created through Steam's Web API with a publisher key, that is, from a server.

So a network lasts as long as at least one of its members is online in it, whichever member that is. Once everyone is offline or has exited at the same time, the network is gone; the apps notice on their next start, tell you, and forget it, and someone has to create a new one. Networks that survive everybody being offline will need a small server of our own (or one with Steam Web API access) that remembers them. That is future work.

Other limitations for now: a member that never comes back keeps its address until it leaves with Leave Network, and there is no way yet to remove it from the network; and a Steam lobby holds at most 8 members online at the same time.

## Testing with the real Steam API

Valve's `steam_api64.dll` is not included in this repository and must not be committed. Copy it from the Steamworks SDK (`redistributable_bin/win64/`) into the repository root, next to `pyproject.toml`; git ignores it there:

```text
SteamVirtualLAN/
    pyproject.toml
    steam_api64.dll
```

A packaged SteamVirtualLAN expects it next to its executable instead:

```text
SteamVirtualLAN/
    SteamVirtualLAN.exe
    steam_api64.dll
```

`STEAMLAN_STEAM_API_DIR` points the app and the scripts at another directory. Checkouts that kept the DLL in a `steamworks` directory need to move it.

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
