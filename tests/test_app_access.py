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
    assert access.parse_message(access.auth_message("7K2QDM9XTE")) == access.Message(
        "auth", code="7K2QDM9XTE"
    )
    assert access.parse_message(access.auth_message("")) == access.Message("auth", code="")
    assert access.parse_message(access.ACCEPTED) == access.Message("accepted")
    assert access.parse_message(access.DENIED) == access.Message("denied")
    assert access.parse_message(access.members_message([USER + 1, USER])) == access.Message(
        "members", members=(USER, USER + 1)
    )
    assert access.parse_message(access.members_message([])) == access.Message("members")


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"hello",
        b"SVL1 AUTH " + b"A" * 11,
        b"SVL1 AUTH \xff\xfe",
        b"SVL1 MEMBERS 1,x",
        b"SVL1 MEMBERS -5",
        b"SVL1 MEMBERS " + str(2**64).encode(),
        b"SVL2 OK",
    ],
)
def test_malformed_messages_are_ignored(data):
    assert access.parse_message(data) is None


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
