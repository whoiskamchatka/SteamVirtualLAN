import ctypes
from dataclasses import dataclass

from steamlan.steam.client import SteamCallback, SteamError
from steamlan.steam.native import (
    CONNECTION_STATUS_CHANGED,
    ConnectionState,
    SteamNetConnectionStatusChangedCallback,
    identity_steam_id,
)


@dataclass(frozen=True)
class ConnectionStatusChange:
    connection: int
    old_state: ConnectionState
    state: ConnectionState
    # None when the remote identity is not a SteamID.
    remote_steam_id: int | None
    # Non-zero when the connection came in on one of our listen sockets.
    listen_socket: int
    end_reason: int
    end_debug: str


def _state(value: int) -> ConnectionState:
    try:
        return ConnectionState(value)
    except ValueError:
        raise SteamError(f"unknown connection state {value}") from None


def decode_networking_event(
    callback: SteamCallback,
) -> ConnectionStatusChange | SteamCallback:
    """Decode connection status changes; any other callback is returned unchanged."""
    if callback.api_call or callback.callback_id != CONNECTION_STATUS_CHANGED:
        return callback

    size = ctypes.sizeof(SteamNetConnectionStatusChangedCallback)
    if len(callback.payload) != size:
        raise SteamError(
            f"callback {callback.callback_id} has {len(callback.payload)} bytes, expected {size}"
        )
    changed = SteamNetConnectionStatusChangedCallback.from_buffer_copy(callback.payload)
    info = changed.m_info
    return ConnectionStatusChange(
        connection=changed.m_hConn,
        old_state=_state(changed.m_eOldState),
        state=_state(info.m_eState),
        remote_steam_id=identity_steam_id(info.m_identityRemote) or None,
        listen_socket=info.m_hListenSocket,
        end_reason=info.m_eEndReason,
        end_debug=info.m_szEndDebug.decode("utf-8", errors="replace"),
    )
