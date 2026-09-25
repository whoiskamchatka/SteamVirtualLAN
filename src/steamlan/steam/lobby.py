import ctypes
from dataclasses import dataclass

from steamlan.steam.client import SteamCallback, SteamError
from steamlan.steam.native import (
    GAME_LOBBY_JOIN_REQUESTED,
    LOBBY_CHAT_UPDATE,
    ChatMemberStateChange,
    GameLobbyJoinRequested,
    LobbyChatUpdate,
)


@dataclass(frozen=True)
class LobbyJoinRequest:
    lobby_id: int
    # None when the join did not come directly through a friend.
    friend_id: int | None


@dataclass(frozen=True)
class LobbyMemberUpdate:
    lobby_id: int
    user_id: int
    changed_by: int
    state: ChatMemberStateChange


def _decode(callback: SteamCallback, struct: type) -> ctypes.Structure:
    if len(callback.payload) != ctypes.sizeof(struct):
        raise SteamError(
            f"callback {callback.callback_id} has {len(callback.payload)} bytes, "
            f"expected {ctypes.sizeof(struct)}"
        )
    return struct.from_buffer_copy(callback.payload)


def decode_lobby_event(
    callback: SteamCallback,
) -> LobbyJoinRequest | LobbyMemberUpdate | SteamCallback:
    """Decode the lobby callbacks SteamLAN uses; anything else is returned unchanged."""
    if callback.api_call:
        return callback

    if callback.callback_id == GAME_LOBBY_JOIN_REQUESTED:
        request = _decode(callback, GameLobbyJoinRequested)
        if not request.m_steamIDLobby:
            raise SteamError("lobby join request without a lobby ID")
        return LobbyJoinRequest(request.m_steamIDLobby, request.m_steamIDFriend or None)

    if callback.callback_id == LOBBY_CHAT_UPDATE:
        update = _decode(callback, LobbyChatUpdate)
        return LobbyMemberUpdate(
            update.m_ulSteamIDLobby,
            update.m_ulSteamIDUserChanged,
            update.m_ulSteamIDMakingChange,
            ChatMemberStateChange(update.m_rgfChatMemberStateChange),
        )

    return callback
