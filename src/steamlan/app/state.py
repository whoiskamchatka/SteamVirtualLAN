"""What SteamVirtualLAN remembers between runs: the network each Steam account is in.

A member stays in a network until it chooses Leave Network. Going offline or
exiting the app keeps the membership, so the next start can go back online in
the same network with the same address. The state lives in the user's local
application data (%LOCALAPPDATA%\\SteamVirtualLAN\\state.json), not next to
the program, and is only written by this module.

The file is JSON with a "version". A file with another version, or one that
can't be read, is set aside as state.json.bak the first time it would be
overwritten, and otherwise ignored.

The access code is stored as well: every member knows it and needs it to
invite others, and to take over coordinating the network (network.py).
"""

import json
import logging
import os
from dataclasses import dataclass, field, replace
from ipaddress import IPv4Address
from pathlib import Path

from steamlan.app import access, roster

log = logging.getLogger(__name__)

STATE_VERSION = 1
APP_DIR_NAME = "SteamVirtualLAN"
STATE_FILE = "state.json"


def default_state_path() -> Path:
    """$STEAMLAN_STATE_DIR/state.json, or %LOCALAPPDATA%\\SteamVirtualLAN\\state.json."""
    override = os.environ.get("STEAMLAN_STATE_DIR")
    if override:
        return Path(override) / STATE_FILE
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / APP_DIR_NAME / STATE_FILE


@dataclass(frozen=True)
class SavedNetwork:
    lobby_id: int
    network_id: str
    access_code: str
    # Everyone in the network with their addresses, as last seen; this
    # member's own entry is its address.
    members: tuple[tuple[int, IPv4Address], ...] = ()
    names: tuple[tuple[int, str], ...] = field(default=(), compare=False)
    # False after Go Offline: the next start stays offline too.
    online: bool = True

    def address_of(self, steam_id: int) -> IPv4Address | None:
        return dict(self.members).get(steam_id)

    def with_online(self, online: bool) -> "SavedNetwork":
        return replace(self, online=online)

    def to_json(self) -> dict:
        return {
            "lobby_id": str(self.lobby_id),
            "network_id": self.network_id,
            "access_code": self.access_code,
            "members": {str(steam_id): str(address) for steam_id, address in self.members},
            "names": {str(steam_id): name for steam_id, name in self.names},
            "online": self.online,
        }

    @classmethod
    def from_json(cls, data: dict) -> "SavedNetwork":
        """ValueError (or KeyError/TypeError) if data isn't a saved network."""
        lobby_id = access.parse_lobby_id(str(data["lobby_id"]))
        network_id = data["network_id"]
        if not isinstance(network_id, str) or not access.is_network_id(network_id):
            raise ValueError("bad network ID")
        code = access.normalize_access_code(data["access_code"])
        members = roster.decode(
            ",".join(f"{steam_id}={address}" for steam_id, address in data["members"].items())
        )
        if members is None:
            raise ValueError("bad members")
        names = tuple(
            (int(steam_id), name)
            for steam_id, name in data.get("names", {}).items()
            if steam_id.isdecimal() and isinstance(name, str)
        )
        online = data.get("online", True)
        if not isinstance(online, bool):
            raise ValueError("bad online flag")
        return cls(lobby_id, network_id, code, tuple(sorted(members.items())), names, online)


class StateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_state_path()
        self._set_aside = False

    def load(self, steam_id: int) -> SavedNetwork | None:
        """The network steam_id was in when SteamVirtualLAN last saved, if any."""
        accounts = self._read()
        entry = accounts.get(str(steam_id))
        if entry is None:
            return None
        try:
            saved = SavedNetwork.from_json(entry)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.warning("Ignored the saved network in %s: %s", self.path, exc)
            return None
        if saved.address_of(steam_id) is None:
            log.warning("Ignored the saved network in %s: no address of our own", self.path)
            return None
        return saved

    def save(self, steam_id: int, network: SavedNetwork | None) -> None:
        """Remember steam_id's network; None forgets it (Leave Network)."""
        accounts = self._read(for_writing=True)
        if network is None:
            if str(steam_id) not in accounts:
                return
            accounts.pop(str(steam_id))
        else:
            accounts[str(steam_id)] = network.to_json()
        data = {"version": STATE_VERSION, "accounts": accounts}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        # Write the new file completely before it replaces the old one, so a
        # crash never leaves half a file behind.
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def _read(self, for_writing: bool = False) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            log.warning("Could not read %s: %s", self.path, exc)
            return {}
        try:
            data = json.loads(text)
            if data.get("version") != STATE_VERSION:
                raise ValueError(f"unsupported version {data.get('version')!r}")
            accounts = data["accounts"]
            if not isinstance(accounts, dict):
                raise ValueError("bad accounts")
            return accounts
        except (ValueError, KeyError, AttributeError, TypeError) as exc:
            if for_writing and not self._set_aside:
                backup = self.path.with_name(self.path.name + ".bak")
                log.warning("Setting aside %s (%s) as %s", self.path, exc, backup.name)
                os.replace(self.path, backup)
                self._set_aside = True
            elif not for_writing:
                log.warning("Ignored %s: %s", self.path, exc)
            return {}
