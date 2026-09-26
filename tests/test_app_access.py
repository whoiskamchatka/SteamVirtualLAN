from ipaddress import IPv4Address

import pytest

from steamlan.app import access

LOBBY = (1 << 56) | (8 << 52) | (0x60000 << 32) | 1234
USER = 76561197960265729


def test_generated_codes_are_valid_and_differ():
    codes = {access.generate_access_code() for _ in range(200)}

    assert len(codes) == 200
    for code in codes:
        assert len(code) == access.CODE_LENGTH
        assert set(code) <= set(access.ALPHABET)
        assert access.normalize_access_code(code) == code


def test_format_access_code():
    assert access.format_access_code("7K2QDM9XTE") == "7K2QD-M9XTE"


@pytest.mark.parametrize(
    "text", ["7K2QD-M9XTE", "7k2qd m9xte", " 7K2QDM9XTE ", "7K2QD-M9XTE".lower()]
)
def test_normalize_access_code(text):
    assert access.normalize_access_code(text) == "7K2QDM9XTE"


def test_normalize_lookalike_letters():
    assert access.normalize_access_code("O1ILO-1ILO1") == "0111011101"


@pytest.mark.parametrize("text", ["", "7K2QD", "7K2QD-M9XTE-1", "7K2QD-M9XT!", "7K2QD-M9XTU"])
def test_malformed_access_code(text):
    with pytest.raises(ValueError, match="10 letters and digits"):
        access.normalize_access_code(text)


def test_access_code_matches():
    assert access.access_code_matches("7K2QDM9XTE", "7K2QDM9XTE")
    assert not access.access_code_matches("7K2QDM9XTF", "7K2QDM9XTE")
    assert not access.access_code_matches("", "7K2QDM9XTE")


def test_parse_lobby_id():
    assert access.parse_lobby_id(f"  {LOBBY} ") == LOBBY


@pytest.mark.parametrize("text", ["", "abc", "0", "-5", str(2**64), str(USER), "12345"])
def test_invalid_lobby_id(text):
    with pytest.raises(ValueError, match="Invalid Lobby ID"):
        access.parse_lobby_id(text)


def test_messages_round_trip():
    address = IPv4Address("10.77.0.7")
    assert access.parse_message(access.auth_message("7K2QDM9XTE")) == access.Message(
        "auth", code="7K2QDM9XTE"
    )
    assert access.parse_message(access.auth_message("7K2QDM9XTE", address)) == access.Message(
        "auth", code="7K2QDM9XTE", address=address
    )
    assert access.parse_message(access.auth_message("")) == access.Message("auth", code="")
    assert access.parse_message(access.auth_message("", address)) == access.Message(
        "auth", address=address
    )
    assert access.parse_message(access.accepted_message("7K2QDM9XTE")) == access.Message(
        "accepted", code="7K2QDM9XTE"
    )
    assert access.parse_message(access.DENIED) == access.Message("denied")
    assert access.parse_message(access.LEAVE) == access.Message("leave")


def test_message_format():
    assert access.auth_message("7K2QDM9XTE", IPv4Address("10.77.0.7")) == (
        b"SVL2 AUTH 7K2QDM9XTE 10.77.0.7"
    )
    assert access.accepted_message("7K2QDM9XTE") == b"SVL2 OK 7K2QDM9XTE"
    assert access.LEAVE == b"SVL2 LEAVE"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"hello",
        b"SVL1 OK",
        b"SVL1 AUTH 7K2QDM9XTE",
        b"SVL1 DENIED",
        b"SVL2 AUTH " + b"A" * 11,
        b"SVL2 AUTH \xff\xfe",
        b"SVL2 AUTH 7K2QDM9XTE 10.77.0.x",
        b"SVL2 AUTH 7K2QDM9XTE 192.168.1.2",
        b"SVL2 AUTH 7K2QDM9XTE 10.77.0.255",
        b"SVL2 AUTH 7K2QDM9XTE 10.77.0.2 more",
        b"SVL2 OK",
        b"SVL2 OK ",
        b"SVL2 OK 7K2QD",
        b"SVL2 OK 7K2QDM9XTU",
        b"SVL2 LEAVE now",
        b"SVL2 PING",
        b"SVL2 PING ",
        b"SVL2 PING 0",
        b"SVL2 PING -1",
        b"SVL2 PING 4294967296",
        b"SVL2 PING 12345678901",
        b"SVL2 PONG 1 2",
        b"SVL2 PONG x",
        b"SVL2 PONG \xd9\xa3",
        b"SVL2 MEMBERS 5=10.77.0.2",
    ],
)
def test_malformed_messages_are_ignored(data):
    assert access.parse_message(data) is None


def test_network_ids():
    ids = {access.generate_network_id() for _ in range(100)}

    assert len(ids) == 100
    assert all(access.is_network_id(network_id) for network_id in ids)
    assert not access.is_network_id("")
    assert not access.is_network_id("0123456789ABCDEF")
    assert not access.is_network_id("0123456789abcde")


def test_invite_connect_string_round_trip():
    connect = access.invite_connect_string(LOBBY, "7K2QDM9XTE")

    assert connect == f"steamvirtuallan:1:{LOBBY}:7K2QDM9XTE"
    assert len(connect.encode()) < 256
    assert access.parse_invite_connect_string(connect) == (LOBBY, "7K2QDM9XTE")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "+connect 1.2.3.4",
        f"steamvirtuallan:2:{LOBBY}:7K2QDM9XTE",
        f"steamvirtuallan:1:{USER}:7K2QDM9XTE",
        f"steamvirtuallan:1:{LOBBY}:short",
        f"steamvirtuallan:1:{LOBBY}",
    ],
)
def test_foreign_or_broken_connect_strings(text):
    with pytest.raises(ValueError):
        access.parse_invite_connect_string(text)


def test_ping_and_pong_round_trip():
    assert access.ping_message(7) == b"SVL2 PING 7"
    assert access.pong_message(7) == b"SVL2 PONG 7"
    assert access.parse_message(access.ping_message(1)) == access.Message("ping", sequence=1)
    assert access.parse_message(access.pong_message(access.MAX_SEQUENCE)) == access.Message(
        "pong", sequence=access.MAX_SEQUENCE
    )
