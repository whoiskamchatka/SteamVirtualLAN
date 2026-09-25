"""Messages between SteamVirtualLAN and its network helper.

The app runs unelevated. Creating and using the Wintun adapter needs
Administrator rights, so a small helper process, started through Windows' UAC
prompt, owns the adapter. The two talk over a local named pipe with
multiprocessing.connection: messages are framed, and before anything else both
sides prove that they know a random key the app generated for this helper.
Only raw bytes are exchanged (send_bytes/recv_bytes); nothing is unpickled.

Every message is one tag byte followed by its body.
"""

from ipaddress import IPv4Address

from steamlan.adapter.ipv4 import VIRTUAL_NETWORK, is_member_address
from steamlan.adapter.wintun import MAX_IP_PACKET_SIZE

# Command line flag that makes "python -m steamlan" (or SteamVirtualLAN.exe)
# run as the helper instead of the app.
HELPER_FLAG = "--network-helper"

# App -> helper
SET_ADDRESS = b"A"  # + the 4-byte address for the adapter, in VIRTUAL_NETWORK
STOP = b"S"  # remove the adapter and exit
# Both ways: an IPv4 packet, to write to the adapter or read from it.
PACKET = b"P"
# Helper -> app
READY = b"R"  # the adapter exists and its packet session is running
ADDRESS_SET = b"A"  # + the 4-byte address the adapter now has
ERROR = b"E"  # + UTF-8 text; the helper stops after sending it
LOG = b"L"  # + UTF-8 text: a log line from the helper

MAX_MESSAGE = 1 + MAX_IP_PACKET_SIZE
PREFIX_LENGTH = VIRTUAL_NETWORK.prefixlen


def split(message: bytes) -> tuple[bytes, bytes]:
    """(tag, body)"""
    return message[:1], message[1:]


def parse_address(body: bytes) -> IPv4Address:
    """The address in a SET_ADDRESS or ADDRESS_SET body; ValueError unless it can
    belong to a member of the virtual network."""
    if len(body) != 4:
        raise ValueError(f"an address has 4 bytes, not {len(body)}")
    address = IPv4Address(body)
    if not is_member_address(address):
        raise ValueError(f"{address} is not an address in {VIRTUAL_NETWORK}")
    return address


def text(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")
