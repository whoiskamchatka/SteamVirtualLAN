"""Access codes for SteamVirtualLAN networks and the messages that check them.

Steam lobbies have no passwords, and lobby metadata can be read by anyone who
knows the lobby ID (ISteamMatchmaking::RequestLobbyData), so a password or its
hash must never be stored there. Instead whoever creates a network generates a
random access code. It only exists in the apps of the network's members, in
their saved network state and in the invites they send. A new member sends the
code to the member currently coordinating the network (see network.py) over
their SteamNetworkingSockets connection, which Steam encrypts and
authenticates; that member checks it and, on success, hands the new member the
code too, so that every member can invite others and take over coordinating.
"""

import hmac
import secrets
from dataclasses import dataclass
from ipaddress import IPv4Address

from steamlan.adapter.ipv4 import is_member_address

# Crockford's base32: no I, L, O or U, so codes survive being read aloud.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 10  # 50 bits

# Lobby IDs are SteamIDs of account type k_EAccountTypeChat.
_CHAT_ACCOUNT_TYPE = 8

# Non-secret lobby metadata that marks our lobbies among other apps using the
# same AppID. The value is the protocol version; apps that speak another one
# treat the lobby as foreign.
LOBBY_MARKER_KEY = "steamvirtuallan"
LOBBY_MARKER_VALUE = "2"
# Random and non-secret: tells a saved network apart from any other lobby that
# might one day have the same ID.
NETWORK_ID_KEY = "svl_network"
NETWORK_ID_LENGTH = 16


def generate_network_id() -> str:
    return secrets.token_hex(NETWORK_ID_LENGTH // 2)


def is_network_id(text: str) -> bool:
    return len(text) == NETWORK_ID_LENGTH and all(char in "0123456789abcdef" for char in text)


def generate_access_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def format_access_code(code: str) -> str:
    return f"{code[:5]}-{code[5:]}"


def normalize_access_code(text: str) -> str:
    """Accept pasted or typed codes: any case, spaces, dashes, O/I/L for 0/1/1."""
    code = text.upper().translate(str.maketrans("OIL", "011", " -"))
    if len(code) != CODE_LENGTH or any(char not in ALPHABET for char in code):
        raise ValueError("An access code has 10 letters and digits")
    return code


def access_code_matches(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


def _account_type(steam_id: int) -> int:
    return (steam_id >> 52) & 0xF


def _parse_id(text: str) -> int:
    text = text.strip()
    if not text.isdecimal() or not 0 < int(text) < 2**64:
        raise ValueError
    return int(text)


def parse_lobby_id(text: str) -> int:
    try:
        lobby_id = _parse_id(text)
    except ValueError:
        raise ValueError("Invalid Lobby ID") from None
    if _account_type(lobby_id) != _CHAT_ACCOUNT_TYPE:
        raise ValueError("Invalid Lobby ID")
    return lobby_id


_INVITE_PREFIX = "steamvirtuallan:1:"


def invite_connect_string(lobby_id: int, code: str) -> str:
    """What a Steam overlay invite carries to the friends it is sent to."""
    return f"{_INVITE_PREFIX}{lobby_id}:{code}"


def parse_invite_connect_string(text: str) -> tuple[int, str]:
    """(lobby_id, access_code) from an accepted invite; ValueError if it isn't ours."""
    if not text.startswith(_INVITE_PREFIX):
        raise ValueError("not a SteamVirtualLAN invite")
    lobby_text, _, code = text[len(_INVITE_PREFIX) :].partition(":")
    return parse_lobby_id(lobby_text), normalize_access_code(code)


# Every control message starts with PREFIX. Control messages are only ever
# parsed here; IP packets travel in their own kind of message (tunnel.py).
PREFIX = b"SVL2 "
DENIED = PREFIX + b"DENIED"
LEAVE = PREFIX + b"LEAVE"


@dataclass(frozen=True)
class Message:
    # "auth": a member asks the coordinator to admit it, with the access code
    #   it has and, when it had one before, the address it would like back.
    # "accepted": the coordinator admitted the member; carries the access code.
    # "denied": the coordinator refused the member.
    # "leave": the sender leaves the network for good (Leave Network).
    kind: str
    code: str = ""
    address: IPv4Address | None = None


def auth_message(code: str, preferred: IPv4Address | None = None) -> bytes:
    message = PREFIX + b"AUTH " + code.encode()
    if preferred is not None:
        message += b" " + str(preferred).encode()
    return message


def accepted_message(code: str) -> bytes:
    return PREFIX + b"OK " + code.encode()


def _parse_code(data: bytes) -> str | None:
    if len(data) <= CODE_LENGTH and data.isascii() and b" " not in data:
        return data.decode()
    return None


def parse_message(data: bytes) -> Message | None:
    """Decode one of our messages; None for anything else or anything malformed."""
    if data == DENIED:
        return Message("denied")
    if data == LEAVE:
        return Message("leave")
    if data.startswith(PREFIX + b"OK "):
        code = _parse_code(data[len(PREFIX) + 3 :])
        if code is not None and len(code) == CODE_LENGTH and set(code) <= set(ALPHABET):
            return Message("accepted", code=code)
        return None
    if data.startswith(PREFIX + b"AUTH "):
        code_part, _, address_part = data[len(PREFIX) + 5 :].partition(b" ")
        code = _parse_code(code_part)
        if code is None:
            return None
        address = None
        if address_part:
            try:
                address = IPv4Address(address_part.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                return None
            if not is_member_address(address):
                return None
        return Message("auth", code=code, address=address)
    return None
