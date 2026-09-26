"""The members of a network and their virtual IP addresses.

The roster lists every member of a network, online or not, with the address
it was given. A member keeps its address while it is offline and only gives it
up with Leave Network; only then can the address be given to someone else.

The roster is kept in the lobby's metadata under ROSTER_KEY, which Steam lets
only the lobby owner write. That is why the member coordinating the network is
always the lobby owner (see network.py): whoever Steam makes the owner can
carry on with the roster the previous one left there. Members read it from
there too, but only while the owner is someone they already trust.
"""

from ipaddress import IPv4Address

from steamlan.adapter.ipv4 import VIRTUAL_NETWORK, is_member_address

ROSTER_KEY = "svl_members"

Roster = dict[int, IPv4Address]


def assign(roster: Roster, steam_id: int, preferred: IPv4Address | None = None) -> IPv4Address:
    """Add steam_id to the roster and return its address.

    A member already in the roster keeps its address. A new one gets
    `preferred` (the address it had before, if any) when that is free, and
    otherwise the lowest free address. ValueError if none is free.
    """
    address = roster.get(steam_id)
    if address is not None:
        return address
    used = set(roster.values())
    if preferred is not None and is_member_address(preferred) and preferred not in used:
        roster[steam_id] = preferred
        return preferred
    for address in VIRTUAL_NETWORK.hosts():
        if address not in used:
            roster[steam_id] = address
            return address
    raise ValueError(f"no free address in {VIRTUAL_NETWORK}")


def encode(roster: Roster) -> str:
    """The roster as lobby metadata: "steamid=address,..." sorted by SteamID."""
    return ",".join(f"{steam_id}={address}" for steam_id, address in sorted(roster.items()))


def decode(text: str) -> Roster | None:
    """The roster from lobby metadata; None if the text is malformed."""
    roster: Roster = {}
    for part in text.split(","):
        if not part:
            continue
        steam_text, separator, address_text = part.partition("=")
        if not separator or not steam_text.isdecimal():
            return None
        steam_id = int(steam_text)
        try:
            address = IPv4Address(address_text)
        except ValueError:
            return None
        if not 0 < steam_id < 2**64 or not is_member_address(address) or steam_id in roster:
            return None
        roster[steam_id] = address
    if len(set(roster.values())) != len(roster):
        return None
    return roster
