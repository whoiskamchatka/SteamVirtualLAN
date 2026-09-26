import pytest


@pytest.fixture(autouse=True)
def no_real_network_helper(monkeypatch):
    """Tests must never start the real helper: that would show a UAC prompt."""

    def refuse(*args, **kwargs):
        pytest.fail("a test tried to start the real network helper")

    monkeypatch.setattr("steamlan.app.controller.AdapterHelper", refuse)
    monkeypatch.setattr("steamlan.adapter.launcher.start_process", refuse)


@pytest.fixture(autouse=True)
def no_real_saved_state(tmp_path_factory, monkeypatch):
    """Tests must never read or write the user's real saved network."""
    monkeypatch.setenv("STEAMLAN_STATE_DIR", str(tmp_path_factory.mktemp("state")))
