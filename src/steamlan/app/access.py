"""Access codes for SteamVirtualLAN networks and the messages that check them.

Steam lobbies have no passwords, and lobby metadata can be read by anyone who
knows the lobby ID (ISteamMatchmaking::RequestLobbyData), so a password or its
hash must never be stored there. Instead the host generates a random access
code that only exists in its memory. A member who joins by lobby ID sends the
code to the host over their SteamNetworkingSockets connection, which Steam
encrypts and authenticates, and the host checks it.
"""

import hmac
import secrets
from dataclasses import dataclass

# Crockford's base32: no I, L, O or U, so codes survive being read aloud.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 10  # 50 bits

# Lobby IDs are SteamIDs of account type k_EAccountTypeChat.
_CHAT_ACCOUNT_TYPE = 8

# Non-secret lobby metadata that marks our lobbies among other apps using the
# same AppID.
LOBBY_MARKER_KEY = "steamvirtuallan"
LOBBY_MARKER_VALUE = "1"


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


_PREFIX = b"SVL1 "
ACCEPTED = _PREFIX + b"OK"
DENIED = _PREFIX + b"DENIED"


@dataclass(frozen=True)
class Message:
    kind: str  # "auth", "accepted", "denied" or "members"
    code: str = ""
    members: tuple[int, ...] = ()


def auth_message(code: str) -> bytes:
    return _PREFIX + b"AUTH " + code.encode()


def members_message(members: list[int]) -> bytes:
    return _PREFIX + b"MEMBERS " + ",".join(str(steam_id) for steam_id in sorted(members)).encode()


def parse_message(data: bytes) -> Message | None:
    """Decode one of our messages; None for anything else or anything malformed."""
    if data == ACCEPTED:
        return Message("accepted")
    if data == DENIED:
        return Message("denied")
    if data.startswith(_PREFIX + b"AUTH "):
        code = data[len(_PREFIX) + 5 :]
        if len(code) <= CODE_LENGTH and code.isascii():
            return Message("auth", code=code.decode())
        return None
    if data.startswith(_PREFIX + b"MEMBERS "):
        text = data[len(_PREFIX) + 8 :].decode("ascii", errors="replace")
        try:
            members = tuple(int(part) for part in text.split(",") if part)
        except ValueError:
            return None
        if all(0 < steam_id < 2**64 for steam_id in members):
            return Message("members", members=members)
    return None
