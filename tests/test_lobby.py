import pytest

from steamlan.steam import (
    ChatMemberStateChange,
    LobbyJoinRequest,
    LobbyMemberUpdate,
    SteamCallback,
    SteamError,
    decode_lobby_event,
)
from steamlan.steam.native import (
    GAME_LOBBY_JOIN_REQUESTED,
    LOBBY_CHAT_UPDATE,
    GameLobbyJoinRequested,
    LobbyChatUpdate,
)

LOBBY_ID = 109775240917097000
HOST_ID = 76561197960265729
GUEST_ID = 76561197960265730


def join_requested(lobby_id=LOBBY_ID, friend_id=HOST_ID):
    return SteamCallback(
        GAME_LOBBY_JOIN_REQUESTED, bytes(GameLobbyJoinRequested(lobby_id, friend_id))
    )


def test_join_request():
    event = decode_lobby_event(join_requested())

    assert event == LobbyJoinRequest(lobby_id=LOBBY_ID, friend_id=HOST_ID)
    assert type(event.lobby_id) is int
    assert type(event.friend_id) is int


def test_join_request_without_friend():
    assert decode_lobby_event(join_requested(friend_id=0)).friend_id is None


def test_join_request_without_lobby():
    with pytest.raises(SteamError, match="without a lobby ID"):
        decode_lobby_event(join_requested(lobby_id=0))


def test_join_request_wrong_size():
    callback = SteamCallback(GAME_LOBBY_JOIN_REQUESTED, bytes(GameLobbyJoinRequested())[:8])

    with pytest.raises(SteamError, match="bytes, expected 16"):
        decode_lobby_event(callback)


def chat_update(state, user_id=GUEST_ID, changed_by=GUEST_ID):
    return SteamCallback(
        LOBBY_CHAT_UPDATE, bytes(LobbyChatUpdate(LOBBY_ID, user_id, changed_by, state))
    )


@pytest.mark.parametrize(
    "state",
    [
        ChatMemberStateChange.ENTERED,
        ChatMemberStateChange.LEFT,
        ChatMemberStateChange.DISCONNECTED,
        ChatMemberStateChange.KICKED,
        ChatMemberStateChange.BANNED,
    ],
)
def test_member_update(state):
    event = decode_lobby_event(chat_update(state))

    assert event == LobbyMemberUpdate(
        lobby_id=LOBBY_ID, user_id=GUEST_ID, changed_by=GUEST_ID, state=state
    )


def test_member_update_combined_flags():
    event = decode_lobby_event(
        chat_update(
            ChatMemberStateChange.LEFT | ChatMemberStateChange.KICKED,
            changed_by=HOST_ID,
        )
    )

    assert ChatMemberStateChange.KICKED in event.state
    assert ChatMemberStateChange.LEFT in event.state
    assert ChatMemberStateChange.ENTERED not in event.state
    assert event.changed_by == HOST_ID


@pytest.mark.parametrize(
    "callback",
    [
        SteamCallback(LOBBY_CHAT_UPDATE, bytes(LobbyChatUpdate())[:28]),
        SteamCallback(LOBBY_CHAT_UPDATE, bytes(LobbyChatUpdate()) + b"\x00"),
    ],
)
def test_wrong_payload_size(callback):
    with pytest.raises(SteamError, match="bytes, expected"):
        decode_lobby_event(callback)


def test_unknown_callback_is_returned_unchanged():
    callback = SteamCallback(304, b"\x01\x02")

    assert decode_lobby_event(callback) is callback


def test_call_result_is_not_decoded_as_event():
    callback = SteamCallback(
        LOBBY_CHAT_UPDATE, bytes(LobbyChatUpdate(LOBBY_ID, GUEST_ID, GUEST_ID, 1)), api_call=5
    )

    assert decode_lobby_event(callback) is callback
