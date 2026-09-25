"""Bring up the SteamVirtualLAN adapter and show the packets Windows sends into it.

Usage, from the repository root:
    python scripts/check_adapter.py [--reply] [--remove-driver]

Everything is done here. If the official wintun.dll isn't there yet it is
downloaded from wintun.net, checked against its published SHA-256 and kept in
the ignored wintun directory. Without Administrator rights the script then
starts itself again through Windows' UAC prompt, in a new window. There the
SteamVirtualLAN adapter is created (Wintun installs its driver on first use),
it gets 10.77.0.1/24, and packets are printed as they arrive.

Run "ping 10.77.0.2" in another terminal to make Windows send packets through
the adapter. With --reply this script answers those pings itself, to show that
packets can be written back to Windows. Stop with Ctrl+C; the adapter is
removed again when the script stops.

--remove-driver also asks Wintun to remove its driver afterwards, if no other
program still uses a Wintun adapter.
"""

import argparse
import ipaddress
import logging
import sys
from pathlib import Path

from steamlan.adapter import AdapterError, VirtualAdapter, WintunLoadError, load_wintun
from steamlan.adapter.bootstrap import ensure_wintun
from steamlan.adapter.ipv4 import describe_packet, ip_version, parse_ipv4_header
from steamlan.adapter.windows import assign_ipv4, is_admin, relaunch_as_admin
from steamlan.adapter.wintun import driver_version, forward_log

log = logging.getLogger("check_adapter")

ADDRESS = ipaddress.IPv4Interface("10.77.0.1/24")
ICMP = 1
ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0


def checksum(data: bytes) -> int:
    """The Internet checksum (RFC 1071)."""
    if len(data) % 2:
        data += b"\0"
    total = sum(int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def echo_reply(packet: bytes) -> bytes | None:
    """An ICMP echo reply for a ping from this machine into the test subnet, else None."""
    if ip_version(packet) != 4:
        return None
    try:
        header = parse_ipv4_header(packet)
    except ValueError:
        return None
    source = ipaddress.IPv4Address(header.source)
    destination = ipaddress.IPv4Address(header.destination)
    if (
        header.protocol != ICMP
        or source != ADDRESS.ip
        or destination not in ADDRESS.network
        or destination in (ADDRESS.ip, ADDRESS.network.broadcast_address)
    ):
        return None
    icmp = bytearray(packet[header.header_length : header.total_length])
    if len(icmp) < 8 or icmp[0] != ICMP_ECHO_REQUEST or icmp[1] != 0:
        return None

    icmp[0] = ICMP_ECHO_REPLY
    icmp[2:4] = b"\0\0"
    icmp[2:4] = checksum(bytes(icmp)).to_bytes(2, "big")

    reply = bytearray(20)
    reply[0] = 0x45  # IPv4, 20-byte header without options
    reply[1] = packet[1]
    reply[2:4] = (20 + len(icmp)).to_bytes(2, "big")
    reply[4:6] = packet[4:6]
    reply[8] = 64  # TTL
    reply[9] = ICMP
    reply[12:16] = destination.packed
    reply[16:20] = source.packed
    reply[10:12] = checksum(bytes(reply)).to_bytes(2, "big")
    return bytes(reply + icmp)


def watch(adapter: VirtualAdapter, reply: bool) -> None:
    while True:
        if not adapter.wait(250):
            continue
        while (packet := adapter.read()) is not None:
            print(describe_packet(packet))
            if reply and (answer := echo_reply(packet)) is not None:
                adapter.write(answer)
                print(f"  wrote echo reply, {len(answer)} bytes")


def elevate(arguments: list[str]) -> int:
    print("Requesting Administrator rights for the network adapter...")
    if not relaunch_as_admin([sys.argv[0], *arguments, "--pause"]):
        print("Administrator rights were not granted; nothing was changed.", file=sys.stderr)
        return 1
    print("Continuing in the Administrator window.")
    return 0


def run(dll: Path, args: argparse.Namespace) -> int:
    wintun = load_wintun(dll)
    forward_log(wintun, logging.getLogger("wintun"))

    adapter = VirtualAdapter(wintun)
    try:
        print(f"Creating {adapter.name} adapter...")
        adapter.open()
        version = driver_version(wintun)
        log.info(
            "%s adapter %s with Wintun driver %s",
            "Created" if adapter.created else "Opened",
            adapter.name,
            version or "not reported",
        )
        added = assign_ipv4(adapter.luid, str(ADDRESS))
        log.info("%s %s on %s only", "Assigned" if added else "Kept", ADDRESS, adapter.name)
        adapter.start()
        print(f"Virtual network ready: {ADDRESS.ip}")
        print(f"In another terminal run: ping {ADDRESS.ip + 1}")
        watch(adapter, args.reply)
    except KeyboardInterrupt:
        pass
    except AdapterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        created = adapter.created
        adapter.close()
        print("Adapter removed" if created else "Adapter released")

    if args.remove_driver:
        removed = wintun.WintunDeleteDriver()
        print("Wintun driver removed" if removed else "Wintun driver still in use; not removed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reply", action="store_true", help="answer pings to 10.77.0.x")
    parser.add_argument("--remove-driver", action="store_true", help="remove Wintun's driver after")
    parser.add_argument("--pause", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    try:
        try:
            dll = ensure_wintun()
            if not is_admin():
                return elevate([arg for arg in sys.argv[1:] if arg != "--pause"])
            return run(dll, args)
        except WintunLoadError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    finally:
        if args.pause:
            input("Press Enter to close this window.")


if __name__ == "__main__":
    sys.exit(main())
