import ctypes
from dataclasses import dataclass

from steamlan.steam.client import SteamCallback, SteamError
from steamlan.steam.native import LOBBY_CHAT_UPDATE, ChatMemberStateChange, LobbyChatUpdate


@dataclass(frozen=True)
class LobbyMemberUpdate:
    lobby_id: int
    user_id: int
    changed_by: int
    state: ChatMemberStateChange


def decode_lobby_event(callback: SteamCallback) -> LobbyMemberUpdate | SteamCallback:
    """Decode lobby member updates; any other callback is returned unchanged."""
    if callback.api_call or callback.callback_id != LOBBY_CHAT_UPDATE:
        return callback

    if len(callback.payload) != ctypes.sizeof(LobbyChatUpdate):
        raise SteamError(
            f"callback {callback.callback_id} has {len(callback.payload)} bytes, "
            f"expected {ctypes.sizeof(LobbyChatUpdate)}"
        )
    update = LobbyChatUpdate.from_buffer_copy(callback.payload)
    return LobbyMemberUpdate(
        update.m_ulSteamIDLobby,
        update.m_ulSteamIDUserChanged,
        update.m_ulSteamIDMakingChange,
        ChatMemberStateChange(update.m_rgfChatMemberStateChange),
    )
