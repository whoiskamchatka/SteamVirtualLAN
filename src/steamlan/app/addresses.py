"""Virtual IP addresses of the members of a network.

Only the host assigns them, and it tells every member the whole mapping from
SteamID to address (access.members_message); members never pick their own.
"""

from ipaddress import IPv4Address

from steamlan.adapter.ipv4 import HOST_ADDRESS, VIRTUAL_NETWORK


class AddressPool:
    """The host's assignments: itself 10.77.0.1, then each admitted member the
    lowest free address from 10.77.0.2. A member keeps its address until it leaves."""

    def __init__(self, host_id: int):
        self.host_id = host_id
        self.assigned: dict[int, IPv4Address] = {host_id: HOST_ADDRESS}

    def assign(self, steam_id: int) -> IPv4Address:
        address = self.assigned.get(steam_id)
        if address is not None:
            return address
        used = set(self.assigned.values())
        for address in VIRTUAL_NETWORK.hosts():
            if address not in used:
                self.assigned[steam_id] = address
                return address
        raise ValueError(f"no free address in {VIRTUAL_NETWORK}")

    def release(self, steam_id: int) -> None:
        if steam_id != self.host_id:
            self.assigned.pop(steam_id, None)
