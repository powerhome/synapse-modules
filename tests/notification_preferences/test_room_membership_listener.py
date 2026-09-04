"""Tests for RoomMembershipListener."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.api.constants import Membership
from synapse.types import UserID

from connect.notification_preferences.notification_preferences import Level
from connect.notification_preferences.room_membership_listener import (
    RoomMembershipListener,
)
from connect.people_conversations import Module as PeopleConversationsModule

BOT_USER_ID = "@bot:powerhrg.com"
USER_ID_STR = "@alice:powerhrg.com"
ROOM_ID = "!room1:powerhrg.com"
NOTIFICATION_PREFERENCE_EVENT_TYPE = "com.powerhrg.connect.notification_preference"
ROOM_FULLY_CREATED_EVENT_TYPE = "com.powerhrg.connect.room_fully_created"

CONFIG = {
    "bot_user_ids": [BOT_USER_ID],
    "room_fully_created_event_type": ROOM_FULLY_CREATED_EVENT_TYPE,
    "notification_preference_event_type": NOTIFICATION_PREFERENCE_EVENT_TYPE,
}


def make_user_id(user_id_str=USER_ID_STR):
    user_id = MagicMock(spec=UserID)
    user_id.to_string.return_value = user_id_str
    user_id.__str__ = lambda self: user_id_str
    return user_id


def make_mock_api():
    api = MagicMock()
    db_pool = MagicMock()
    api._hs.get_datastores.return_value.main.db_pool = db_pool
    return api


fake_people_conversations_callback = MagicMock(
    __func__=PeopleConversationsModule.after_create_room
)


@patch(
    "connect.notification_preferences.room_membership_listener.after_create_room_callbacks",
    [fake_people_conversations_callback],
)
def build_listener():
    api = make_mock_api()
    listener = RoomMembershipListener(api, CONFIG)
    return listener


class TestInit(unittest.TestCase):
    @patch(
        "connect.notification_preferences.room_membership_listener.after_create_room_callbacks",
        [],
    )
    def test__raises_when_people_conversations_not_registered(self):
        api = make_mock_api()
        with self.assertRaises(RuntimeError):
            RoomMembershipListener(api, CONFIG)


class TestAfterCreateRoom(unittest.IsolatedAsyncioTestCase):
    async def test__skips_bot_users(self):
        listener = build_listener()
        listener._set_notification_preference = AsyncMock()

        bot_user = make_user_id(BOT_USER_ID)
        await listener._after_create_room(bot_user, ROOM_ID, {})

        listener._set_notification_preference.assert_not_called()

    async def test__non_direct_room_sets_default_preference(self):
        listener = build_listener()
        listener._set_notification_preference = AsyncMock()

        user_id = make_user_id()
        await listener._after_create_room(user_id, ROOM_ID, {})

        listener._set_notification_preference.assert_called_once_with(
            USER_ID_STR, ROOM_ID, Level(Level.ALL_AND_ME)
        )

    async def test__direct_room_with_2_members_skips(self):
        listener = build_listener()
        listener.people_conversation_store.get_member_count = AsyncMock(return_value=2)
        listener._set_notification_preference = AsyncMock()

        user_id = make_user_id()
        await listener._after_create_room(user_id, ROOM_ID, {"is_direct": True})

        listener._set_notification_preference.assert_not_called()

    async def test__direct_room_with_more_than_2_members_sets_group_preference(self):
        listener = build_listener()
        listener.people_conversation_store.get_member_count = AsyncMock(return_value=3)
        listener._set_notification_preference = AsyncMock()

        user_id = make_user_id()
        await listener._after_create_room(user_id, ROOM_ID, {"is_direct": True})

        listener._set_notification_preference.assert_called_once_with(
            USER_ID_STR, ROOM_ID, Level(Level.EVERY_MESSAGE)
        )


def make_event(
    sender=USER_ID_STR,
    event_type="m.room.member",
    membership=Membership.JOIN,
    room_id=ROOM_ID,
):
    event = MagicMock()
    event.sender = sender
    event.type = event_type
    event.membership = membership
    event.room_id = room_id
    return event


def make_created_event(is_direct=False):
    event = MagicMock()
    event.content = {"is_direct": is_direct}
    return event


class TestOnNewEvent(unittest.IsolatedAsyncioTestCase):
    async def test__skips_bot_sender(self):
        listener = build_listener()
        listener._set_notification_preference = AsyncMock()

        event = make_event(sender=BOT_USER_ID)
        await listener._on_new_event(event, {})

        listener._set_notification_preference.assert_not_called()

    async def test__skips_non_member_events(self):
        listener = build_listener()
        listener._set_notification_preference = AsyncMock()

        event = make_event(event_type="m.room.message")
        await listener._on_new_event(event, {})

        listener._set_notification_preference.assert_not_called()

    async def test__skips_non_join_membership(self):
        listener = build_listener()
        listener._set_notification_preference = AsyncMock()

        event = make_event(membership=Membership.LEAVE)
        await listener._on_new_event(event, {})

        listener._set_notification_preference.assert_not_called()

    async def test__skips_when_no_created_event(self):
        listener = build_listener()
        listener._set_notification_preference = AsyncMock()

        event = make_event()
        await listener._on_new_event(event, {})

        listener._set_notification_preference.assert_not_called()

    async def test__non_direct_room_sets_default_preference(self):
        listener = build_listener()
        listener._set_notification_preference = AsyncMock()

        event = make_event()
        created_event = make_created_event(is_direct=False)
        state_events = {(ROOM_FULLY_CREATED_EVENT_TYPE, ""): created_event}

        await listener._on_new_event(event, state_events)

        listener._set_notification_preference.assert_called_once_with(
            USER_ID_STR, ROOM_ID, Level(Level.ALL_AND_ME)
        )

    async def test__direct_room_with_2_members_skips(self):
        listener = build_listener()
        listener.people_conversation_store.get_member_count = AsyncMock(return_value=2)
        listener._set_notification_preference = AsyncMock()

        event = make_event()
        created_event = make_created_event(is_direct=True)
        state_events = {(ROOM_FULLY_CREATED_EVENT_TYPE, ""): created_event}

        await listener._on_new_event(event, state_events)

        listener._set_notification_preference.assert_not_called()

    async def test__direct_room_with_more_than_2_members_sets_group_preference(self):
        listener = build_listener()
        listener.people_conversation_store.get_member_count = AsyncMock(return_value=3)
        listener._set_notification_preference = AsyncMock()

        event = make_event()
        created_event = make_created_event(is_direct=True)
        state_events = {(ROOM_FULLY_CREATED_EVENT_TYPE, ""): created_event}

        await listener._on_new_event(event, state_events)

        listener._set_notification_preference.assert_called_once_with(
            USER_ID_STR, ROOM_ID, Level(Level.EVERY_MESSAGE)
        )


if __name__ == "__main__":
    unittest.main()
