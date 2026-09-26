import json
from ipaddress import IPv4Address
from pathlib import Path

import pytest

from steamlan.app import state
from steamlan.app.state import STATE_VERSION, SavedNetwork, StateStore

ME = 76561197960265730
FRIEND = 76561197960265729
LOBBY = (1 << 56) | (8 << 52) | (0x60000 << 32) | 1234
A1, A2 = IPv4Address("10.77.0.1"), IPv4Address("10.77.0.2")

NETWORK = SavedNetwork(
    LOBBY,
    "0123456789abcdef",
    "7K2QDM9XTE",
    ((FRIEND, A1), (ME, A2)),
    ((FRIEND, "Friend"),),
    online=True,
)


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "SteamVirtualLAN" / "state.json")


def test_round_trip(store):
    store.save(ME, NETWORK)

    loaded = store.load(ME)
    assert loaded == NETWORK
    assert loaded.names == NETWORK.names
    assert loaded.address_of(ME) == A2


def test_nothing_saved(store):
    assert store.load(ME) is None
    assert not store.path.exists()


def test_file_format_is_versioned_json(store):
    store.save(ME, NETWORK.with_online(False))

    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data == {
        "version": STATE_VERSION,
        "accounts": {
            str(ME): {
                "lobby_id": str(LOBBY),
                "network_id": "0123456789abcdef",
                "access_code": "7K2QDM9XTE",
                "members": {str(FRIEND): "10.77.0.1", str(ME): "10.77.0.2"},
                "names": {str(FRIEND): "Friend"},
                "online": False,
            }
        },
    }
    assert not store.path.with_name("state.json.tmp").exists()


def test_accounts_are_kept_apart(store):
    other = SavedNetwork(LOBBY + 1, "fedcba9876543210", "AAAAAAAAAA", ((FRIEND, A1),))
    store.save(ME, NETWORK)
    store.save(FRIEND, other)

    assert store.load(ME) == NETWORK
    assert store.load(FRIEND) == other

    store.save(ME, None)
    assert store.load(ME) is None
    assert store.load(FRIEND) == other


def test_forgetting_what_was_never_saved_writes_nothing(store):
    store.save(ME, None)

    assert not store.path.exists()


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"version": 99, "accounts": {}}',
        '{"accounts": {}}',
        '{"version": 1, "accounts": []}',
    ],
)
def test_unreadable_or_other_version_is_ignored_then_set_aside(store, content):
    store.path.parent.mkdir(parents=True)
    store.path.write_text(content, encoding="utf-8")

    assert store.load(ME) is None
    assert store.path.read_text(encoding="utf-8") == content

    store.save(ME, NETWORK)

    assert store.path.with_name("state.json.bak").read_text(encoding="utf-8") == content
    assert store.load(ME) == NETWORK


@pytest.mark.parametrize(
    "change",
    [
        {"lobby_id": "12345"},
        {"network_id": "not hex"},
        {"access_code": "short"},
        {"members": {"1": "192.168.1.1"}},
        {"members": {str(FRIEND): "10.77.0.1"}},  # no address of our own
        {"online": "yes"},
        {"lobby_id": None},
    ],
)
def test_broken_saved_network_is_ignored(store, change):
    store.save(ME, NETWORK)
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["accounts"][str(ME)].update(change)
    store.path.write_text(json.dumps(data), encoding="utf-8")

    assert store.load(ME) is None


def test_default_location_is_local_app_data(monkeypatch, tmp_path):
    monkeypatch.delenv("STEAMLAN_STATE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert state.default_state_path() == tmp_path / "SteamVirtualLAN" / "state.json"


def test_default_location_without_local_app_data(monkeypatch):
    monkeypatch.delenv("STEAMLAN_STATE_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    path = state.default_state_path()

    assert path == Path.home() / "AppData" / "Local" / "SteamVirtualLAN" / "state.json"


def test_tests_never_touch_the_real_state(monkeypatch):
    repository = Path(__file__).resolve().parent.parent

    path = state.default_state_path()

    assert "state" in path.parent.name
    assert repository not in path.parents
