"""Tests for event auth-related monkey patches."""

import time
import unittest
from unittest.mock import AsyncMock, Mock, call, create_autospec

from synapse.api.errors import AuthError
from synapse.events import EventBase, make_event_from_dict
from synapse.module_api import ModuleApi
from synapse.server import HomeServer

import monkey_patches.event_auth

base_content = {
    "ban": 50,
    "events": {
        "m.room.avatar": 50,
        "m.room.canonical_alias": 50,
        "m.room.encryption": 100,
        "m.room.history_visibility": 100,
        "m.room.name": 50,
        "m.room.power_levels": 100,
        "m.room.server_acl": 100,
        "m.room.tombstone": 100,
    },
    "events_default": 0,
    "historical": 100,
    "invite": 0,
    "kick": 50,
    "redact": 50,
    "state_default": 50,
    "users_default": 0,
}

test_room_id = "!abc:localhost"

a = "@a:localhost"
b = "@b:localhost"
c = "@c:localhost"
bot = "@bot:localhost"
audiences_bot = "@audiences_bot:localhost"

a_joined = make_event_from_dict(
    {
        "event_id": str(time.time_ns()),
        "type": "m.room.member",
        "content": {"membership": "join"},
        "room_id": test_room_id,
        "sender": a,
        "state_key": a,
    }
)

room_create = make_event_from_dict(
    {
        "event_id": str(time.time_ns()),
        "type": "m.room.create",
        "content": {"creator": a},
        "room_id": test_room_id,
        "sender": a,
        "state_key": "",
    }
)


def create_power_levels_event(sender: str, content: dict) -> EventBase:
    return make_event_from_dict(
        {
            "event_id": str(time.time_ns()),
            "type": "m.room.power_levels",
            "content": content,
            "room_id": test_room_id,
            "sender": sender,
            "state_key": "",
        }
    )


def create_leave_event(sender: str, target: str) -> EventBase:
    return make_event_from_dict(
        {
            "event_id": str(time.time_ns()),
            "type": "m.room.member",
            "content": {"membership": "leave"},
            "room_id": test_room_id,
            "sender": sender,
            "state_key": target,
        }
    )


class EventAuthPatchesFindSenderInRoomTestSuite(unittest.IsolatedAsyncioTestCase):
    def create_patches(self) -> monkey_patches.event_auth.EventAuthPatches:
        api = create_autospec(ModuleApi)
        api.worker_name = None
        api.server_name = "localhost"
        api._hs = create_autospec(HomeServer)
        return monkey_patches.event_auth.EventAuthPatches({}, api)

    async def test_sender_is_bot_if_bot_is_member_and_admin(self):
        patches = self.create_patches()
        patches.store.check_local_user_in_room = AsyncMock(return_value=True)

        leave_event = create_leave_event(a, b)
        content = {"users": {a: 100, audiences_bot: 100}}
        sender = await patches._find_sender_in_room(leave_event, content)

        self.assertEqual(sender, audiences_bot)

    async def test_sender_is_other_admin_if_bot_is_not_admin(self):
        patches = self.create_patches()
        patches.store.check_local_user_in_room = AsyncMock(return_value=True)

        leave_event = create_leave_event(c, b)
        content = {"users": {a: 100, c: 100, audiences_bot: 0}}
        sender = await patches._find_sender_in_room(leave_event, content)

        patches.store.check_local_user_in_room.assert_has_calls(
            [call(audiences_bot, test_room_id), call(a, test_room_id)]
        )
        self.assertEqual(sender, a)

    async def test_sender_is_other_admin_if_bot_is_not_member(self):
        async def check_local_user_in_room(user_id: str, room_id: str):
            assert room_id == test_room_id
            if user_id == audiences_bot:
                return False
            return True

        patches = self.create_patches()
        patches.store.check_local_user_in_room = AsyncMock(
            side_effect=check_local_user_in_room
        )

        leave_event = create_leave_event(c, b)
        content = {"users": {a: 100, c: 100, audiences_bot: 100}}
        sender = await patches._find_sender_in_room(leave_event, content)

        patches.store.check_local_user_in_room.assert_has_calls(
            [call(audiences_bot, test_room_id), call(a, test_room_id)]
        )
        self.assertEqual(sender, a)

    async def test_sender_is_yet_another_admin_if_bot_and_first_admin_are_not_members(
        self,
    ):
        async def check_local_user_in_room(user_id: str, room_id: str):
            assert room_id == test_room_id
            if user_id == audiences_bot:
                return False
            if user_id == a:
                return False
            return True

        patches = self.create_patches()
        patches.store.check_local_user_in_room = AsyncMock(
            side_effect=check_local_user_in_room
        )

        leave_event = create_leave_event(c, b)
        content = {"users": {a: 100, c: 100, audiences_bot: 100}}
        sender = await patches._find_sender_in_room(leave_event, content)

        patches.store.check_local_user_in_room.assert_has_calls(
            [
                call(audiences_bot, test_room_id),
                call(a, test_room_id),
                call(c, test_room_id),
            ]
        )
        self.assertEqual(sender, c)

    async def test_sender_is_leave_event_sender_if_no_others(self):
        patches = self.create_patches()
        patches.store.check_local_user_in_room = AsyncMock(return_value=False)

        leave_event = create_leave_event(a, b)
        content = {"users": {a: 100}}
        sender = await patches._find_sender_in_room(leave_event, content)

        self.assertEqual(sender, a)


class EventAuthPatchesCheckPowerLevelsTestSuite(unittest.TestCase):
    def test_human_admin_can_demote_other_human_admin(self):
        monkey_patches.event_auth.bot_ids = set()

        current_content = {**base_content, "users": {a: 100, b: 100}}
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {
            ("m.room.create", ""): room_create,
            ("m.room.power_levels", ""): current_power_levels,
        }

        new_content = {**base_content, "users": {a: 100}}
        event = create_power_levels_event(a, new_content)

        monkey_patches.event_auth._check_power_levels(Mock(), event, auth_events)

    def test_human_admin_cannot_demote_bot_admin(self):
        monkey_patches.event_auth.bot_ids = {bot}

        current_content = {**base_content, "users": {a: 100, bot: 100}}
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {("m.room.power_levels", ""): current_power_levels}

        new_content = {**base_content, "users": {a: 100}}
        event = create_power_levels_event(a, new_content)

        with self.assertRaises(AuthError):
            monkey_patches.event_auth._check_power_levels(Mock(), event, auth_events)

    def test_human_admin_can_demote_self_if_not_last_human_admin(self):
        monkey_patches.event_auth.bot_ids = {bot}

        current_content = {
            **base_content,
            "users": {a: 100, b: 100, bot: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {
            ("m.room.create", ""): room_create,
            ("m.room.power_levels", ""): current_power_levels,
        }

        new_content = {**base_content, "users": {b: 100, bot: 100}}
        event = create_power_levels_event(a, new_content)

        monkey_patches.event_auth._check_power_levels(Mock(), event, auth_events)

    def test_human_admin_cannot_demote_self_if_last_human_admin(self):
        monkey_patches.event_auth.bot_ids = {bot}

        current_content = {
            **base_content,
            "users": {a: 100, bot: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {("m.room.power_levels", ""): current_power_levels}

        new_content = {**base_content, "users": {bot: 100}}
        event = create_power_levels_event(a, new_content)

        with self.assertRaises(AuthError):
            monkey_patches.event_auth._check_power_levels(Mock(), event, auth_events)

    def test_bot_can_demote_last_human_admin(self):
        monkey_patches.event_auth.bot_ids = {bot}

        current_content = {
            **base_content,
            "users": {a: 100, bot: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {
            ("m.room.create", ""): room_create,
            ("m.room.power_levels", ""): current_power_levels,
        }

        new_content = {**base_content, "users": {bot: 100}}
        event = create_power_levels_event(bot, new_content)

        monkey_patches.event_auth._check_power_levels(Mock(), event, auth_events)

    def test_last_human_admin_can_still_add_users_to_power_levels(self):
        monkey_patches.event_auth.bot_ids = {bot}

        current_content = {
            **base_content,
            "users": {a: 100, bot: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {
            ("m.room.create", ""): room_create,
            ("m.room.power_levels", ""): current_power_levels,
        }

        new_content = {**base_content, "users": {a: 100, b: 100, bot: 100}}
        event = create_power_levels_event(a, new_content)

        monkey_patches.event_auth._check_power_levels(Mock(), event, auth_events)

    def test_human_admin_cannot_demote_last_2_human_admins_at_once(self):
        monkey_patches.event_auth.bot_ids = set()

        current_content = {
            **base_content,
            "users": {a: 100, b: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {("m.room.power_levels", ""): current_power_levels}

        new_content = {**base_content, "users": {}}
        event = create_power_levels_event(a, new_content)

        with self.assertRaises(AuthError):
            monkey_patches.event_auth._check_power_levels(Mock(), event, auth_events)


class EventAuthPatchesIsMembershipChangeAllowedTestSuite(unittest.TestCase):
    def test_human_admin_can_kick_other_human_admin(self):
        monkey_patches.event_auth.bot_ids = set()

        current_content = {
            **base_content,
            "users": {a: 100, b: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {
            ("m.room.create", ""): room_create,
            ("m.room.power_levels", ""): current_power_levels,
            ("m.room.member", a): a_joined,
        }

        event = create_leave_event(a, b)

        monkey_patches.event_auth._is_membership_change_allowed(
            Mock(), event, auth_events
        )

    def test_human_admin_cannot_kick_bot_admin(self):
        monkey_patches.event_auth.bot_ids = {bot}

        current_content = {
            **base_content,
            "users": {a: 100, bot: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {
            ("m.room.power_levels", ""): current_power_levels,
            ("m.room.member", a): a_joined,
        }

        event = create_leave_event(a, bot)

        with self.assertRaises(AuthError):
            monkey_patches.event_auth._is_membership_change_allowed(
                Mock(), event, auth_events
            )

    def test_human_admin_can_kick_self_if_not_last_human_admin(self):
        monkey_patches.event_auth.bot_ids = {bot}

        current_content = {
            **base_content,
            "users": {a: 100, b: 100, bot: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {
            ("m.room.create", ""): room_create,
            ("m.room.power_levels", ""): current_power_levels,
            ("m.room.member", a): a_joined,
        }

        event = create_leave_event(a, a)

        monkey_patches.event_auth._is_membership_change_allowed(
            Mock(), event, auth_events
        )

    def test_human_admin_cannot_kick_self_if_last_human_admin(self):
        monkey_patches.event_auth.bot_ids = {bot}

        current_content = {
            **base_content,
            "users": {a: 100, bot: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {
            ("m.room.create", ""): room_create,
            ("m.room.power_levels", ""): current_power_levels,
            ("m.room.member", a): a_joined,
        }

        event = create_leave_event(a, a)

        with self.assertRaises(AuthError):
            monkey_patches.event_auth._is_membership_change_allowed(
                Mock(), event, auth_events
            )

    def test_bot_can_kick_last_human_admin(self):
        monkey_patches.event_auth.bot_ids = {bot}

        current_content = {
            **base_content,
            "users": {a: 100, bot: 100},
        }
        current_power_levels = create_power_levels_event(a, current_content)
        auth_events = {
            ("m.room.power_levels", ""): current_power_levels,
            ("m.room.member", a): a_joined,
        }

        event = create_leave_event(bot, a)

        monkey_patches.event_auth._is_membership_change_allowed(
            Mock(), event, auth_events
        )


if __name__ == "__main__":
    unittest.main()
