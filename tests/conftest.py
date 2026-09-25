import pytest


@pytest.fixture(autouse=True)
def no_real_network_helper(monkeypatch):
    """Tests must never start the real helper: that would show a UAC prompt."""

    def refuse(*args, **kwargs):
        pytest.fail("a test tried to start the real network helper")

    monkeypatch.setattr("steamlan.app.controller.AdapterHelper", refuse)
    monkeypatch.setattr("steamlan.adapter.launcher.start_process", refuse)
