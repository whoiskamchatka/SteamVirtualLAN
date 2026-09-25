import pytest

from steamlan.steam import (
    ConnectionState,
    ConnectionStatusChange,
    SteamCallback,
    SteamError,
    decode_networking_event,
)
from steamlan.steam.native import (
    CONNECTION_STATUS_CHANGED,
    SteamNetConnectionStatusChangedCallback,
)

REMOTE_ID = 76561197960265730
CONNECTION = 0x1234
LISTEN_SOCKET = 0x55


def status_changed(
    state=ConnectionState.CONNECTING,
    old_state=ConnectionState.NONE,
    identity_type=16,
    listen_socket=LISTEN_SOCKET,
    end_reason=0,
    end_debug=b"",
):
    changed = SteamNetConnectionStatusChangedCallback()
    changed.m_hConn = CONNECTION
    changed.m_eOldState = old_state
    info = changed.m_info
    info.m_identityRemote.m_eType = identity_type
    info.m_identityRemote.m_cbSize = 8
    info.m_identityRemote.m_data[:8] = REMOTE_ID.to_bytes(8, "little")
    info.m_hListenSocket = listen_socket
    info.m_eState = state
    info.m_eEndReason = end_reason
    info.m_szEndDebug = end_debug
    return SteamCallback(CONNECTION_STATUS_CHANGED, bytes(changed))


def test_incoming_connection():
    event = decode_networking_event(status_changed())

    assert event == ConnectionStatusChange(
        connection=CONNECTION,
        old_state=ConnectionState.NONE,
        state=ConnectionState.CONNECTING,
        remote_steam_id=REMOTE_ID,
        listen_socket=LISTEN_SOCKET,
        end_reason=0,
        end_debug="",
    )
    assert type(event.remote_steam_id) is int


def test_closed_connection():
    event = decode_networking_event(
        status_changed(
            state=ConnectionState.CLOSED_BY_PEER,
            old_state=ConnectionState.CONNECTED,
            listen_socket=0,
            end_reason=1000,
            end_debug=b"peer closed",
        )
    )

    assert event.state is ConnectionState.CLOSED_BY_PEER
    assert event.old_state is ConnectionState.CONNECTED
    assert event.listen_socket == 0
    assert event.end_reason == 1000
    assert event.end_debug == "peer closed"


def test_remote_identity_not_a_steam_id():
    assert decode_networking_event(status_changed(identity_type=1)).remote_steam_id is None


def test_unknown_connection_state():
    with pytest.raises(SteamError, match="unknown connection state 42"):
        decode_networking_event(status_changed(state=42))


@pytest.mark.parametrize("size_change", [-1, -8, 1])
def test_wrong_payload_size(size_change):
    payload = status_changed().payload
    payload = payload[:size_change] if size_change < 0 else payload + b"\x00" * size_change

    with pytest.raises(SteamError, match="bytes, expected 712"):
        decode_networking_event(SteamCallback(CONNECTION_STATUS_CHANGED, payload))


def test_other_callback_is_returned_unchanged():
    callback = SteamCallback(506, b"\x00" * 32)

    assert decode_networking_event(callback) is callback


def test_call_result_is_not_decoded_as_event():
    callback = SteamCallback(CONNECTION_STATUS_CHANGED, status_changed().payload, api_call=5)

    assert decode_networking_event(callback) is callback
