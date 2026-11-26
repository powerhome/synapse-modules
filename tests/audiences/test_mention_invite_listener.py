"""Tests for MentionInviteListener: a public-room @mention adds the mentioned user."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from synapse.events import make_event_from_dict

from connect.audiences.mention_invite_listener import MentionInviteListener

ROOM = "!room:localhost"
SENDER = "@sender:localhost"
OUTSIDER = "@outsider:localhost"
SECOND_OUTSIDER = "@second_outsider:localhost"
MEMBER = "@member:localhost"
INVITEE = "@invitee:localhost"
BOT = "@connect_bot:localhost"

CONFIG = {
    "hs_token": "hs_token",  # noqa: S105
    "idp_id": "nitro",
    "bot_user_ids": [BOT, "@audiences_bot:localhost"],
}


class BackgroundProcesses:
    """Stands in for ModuleApi.run_as_background_process.

    Records each spawned coroutine so a test can wait for the work it started.
    """

    def __init__(self):
        self.pending = []

    def run_as_background_process(self, desc, func, *args, **kwargs):
        self.pending.append(asyncio.ensure_future(func(*args, **kwargs)))

    async def drain(self):
        await asyncio.gather(*self.pending)


def make_listener(deactivated=()):
    background = BackgroundProcesses()
    api = MagicMock()
    api.worker_name = None
    api.run_as_background_process = background.run_as_background_process
    audiences = MagicMock()
    audiences.update = AsyncMock(return_value={})

    async def deactivated_among(user_ids):
        return set(user_ids) & set(deactivated)

    listener = MentionInviteListener(
        api, CONFIG, audiences=audiences, deactivated_among=deactivated_among
    )
    return listener, audiences, background


def message(content, sender=SENDER, device_id="DEVICE", event_type="m.room.message"):
    internal_metadata = {"device_id": device_id} if device_id else {}
    return make_event_from_dict(
        {
            "type": event_type,
            "room_id": ROOM,
            "sender": sender,
            "content": content,
            "event_id": "$message",
            "origin_server_ts": 0,
        },
        internal_metadata_dict=internal_metadata,
    )


def mentioning(*user_ids, body="hello"):
    return {
        "msgtype": "m.text",
        "body": body,
        "m.mentions": {"user_ids": list(user_ids)},
    }


def state_event(event_type, state_key, content):
    return make_event_from_dict(
        {
            "type": event_type,
            "state_key": state_key,
            "room_id": ROOM,
            "sender": SENDER,
            "content": content,
            "event_id": f"${event_type}:{state_key}",
            "origin_server_ts": 0,
        },
    )


def room_state(join_rule="public", memberships=None):
    memberships = {SENDER: "join", MEMBER: "join", INVITEE: "invite"} | (
        memberships or {}
    )
    state = {
        ("m.room.join_rules", ""): state_event(
            "m.room.join_rules", "", {"join_rule": join_rule}
        )
    }
    for user_id, membership in memberships.items():
        state[("m.room.member", user_id)] = state_event(
            "m.room.member", user_id, {"membership": membership}
        )
    return state


def invited_user_ids(audiences):
    return [
        sorted(user.to_string() for user in call.args[1])
        for call in audiences.update.await_args_list
    ]


class PublicRoomMentionTestSuite(unittest.IsolatedAsyncioTestCase):
    """A joined member's mention adds the non-members they mention."""

    async def test_mentioning_a_non_member_adds_them_to_the_room(self):
        listener, audiences, background = make_listener()

        await listener.on_new_event(message(mentioning(OUTSIDER)), room_state())
        await background.drain()

        audiences.update.assert_awaited_once()
        room_id, users, action = audiences.update.await_args.args
        self.assertEqual(room_id, ROOM)
        self.assertEqual([user.to_string() for user in users], [OUTSIDER])
        self.assertEqual(action, "invite")

    async def test_several_non_members_are_added_in_one_request(self):
        listener, audiences, background = make_listener()

        await listener.on_new_event(
            message(mentioning(OUTSIDER, SECOND_OUTSIDER)), room_state()
        )
        await background.drain()

        self.assertEqual(
            invited_user_ids(audiences), [sorted([OUTSIDER, SECOND_OUTSIDER])]
        )

    async def test_only_the_non_members_among_the_mentions_are_added(self):
        listener, audiences, background = make_listener()

        await listener.on_new_event(
            message(mentioning(MEMBER, OUTSIDER, INVITEE, SENDER, BOT)), room_state()
        )
        await background.drain()

        self.assertEqual(invited_user_ids(audiences), [[OUTSIDER]])

    async def test_someone_who_left_the_room_is_added_back(self):
        listener, audiences, background = make_listener()

        await listener.on_new_event(
            message(mentioning(OUTSIDER)), room_state(memberships={OUTSIDER: "leave"})
        )
        await background.drain()

        self.assertEqual(invited_user_ids(audiences), [[OUTSIDER]])

    async def test_a_deactivated_user_is_never_added(self):
        listener, audiences, background = make_listener(deactivated={OUTSIDER})

        await listener.on_new_event(
            message(mentioning(OUTSIDER, SECOND_OUTSIDER)), room_state()
        )
        await background.drain()

        self.assertEqual(invited_user_ids(audiences), [[SECOND_OUTSIDER]])

    async def test_a_malformed_mention_does_not_block_the_valid_ones(self):
        listener, audiences, background = make_listener()

        await listener.on_new_event(
            message(mentioning("not-a-user-id", OUTSIDER)), room_state()
        )
        await background.drain()

        self.assertEqual(invited_user_ids(audiences), [[OUTSIDER]])

    async def test_one_message_adds_every_mentioned_non_member(self):
        listener, audiences, background = make_listener()
        outsiders = [f"@outsider{i:02}:localhost" for i in range(25)]

        await listener.on_new_event(message(mentioning(*outsiders)), room_state())
        await background.drain()

        self.assertEqual(invited_user_ids(audiences), [outsiders])

    async def test_a_failed_add_does_not_raise(self):
        listener, audiences, background = make_listener()
        audiences.update.side_effect = RuntimeError("CPG is down")

        await listener.on_new_event(message(mentioning(OUTSIDER)), room_state())
        await background.drain()

        audiences.update.assert_awaited_once()


class NoAddTestSuite(unittest.IsolatedAsyncioTestCase):
    """Only a member's mention of a non-member in a public room reaches CPG.

    Even a no-op extra_users call schedules a full room evaluation there, so an
    ordinary message must cost nothing (JEDI-973).
    """

    async def assert_nobody_added(self, event, state):
        listener, audiences, background = make_listener()

        await listener.on_new_event(event, state)
        await background.drain()

        audiences.update.assert_not_awaited()
        self.assertEqual(background.pending, [])

    async def test_a_message_without_mentions(self):
        await self.assert_nobody_added(
            message({"msgtype": "m.text", "body": "hello"}), room_state()
        )

    async def test_mentioning_only_people_already_in_the_room(self):
        await self.assert_nobody_added(
            message(mentioning(MEMBER, INVITEE, SENDER)), room_state()
        )

    async def test_mentioning_only_bots(self):
        await self.assert_nobody_added(message(mentioning(BOT)), room_state())

    async def test_an_at_room_mention(self):
        content = {"msgtype": "m.text", "body": "@room", "m.mentions": {"room": True}}

        await self.assert_nobody_added(message(content), room_state())

    async def test_a_private_room(self):
        await self.assert_nobody_added(
            message(mentioning(OUTSIDER)), room_state(join_rule="invite")
        )

    async def test_a_sender_who_is_not_joined(self):
        await self.assert_nobody_added(
            message(mentioning(OUTSIDER)), room_state(memberships={SENDER: "leave"})
        )

    async def test_an_edit(self):
        content = mentioning(OUTSIDER) | {
            "m.new_content": mentioning(OUTSIDER),
            "m.relates_to": {"rel_type": "m.replace", "event_id": "$original"},
        }

        await self.assert_nobody_added(message(content), room_state())

    async def test_a_message_relayed_from_v2_by_the_bridge(self):
        # The bridge sends as an appservice, so its events carry no device.
        # V2-origin mentions are added by V2's own republish (JEDI-1130).
        await self.assert_nobody_added(
            message(mentioning(OUTSIDER), device_id=None), room_state()
        )

    async def test_a_non_message_event(self):
        await self.assert_nobody_added(
            message(mentioning(OUTSIDER), event_type="m.reaction"), room_state()
        )

    async def test_a_state_event(self):
        await self.assert_nobody_added(
            state_event("m.room.topic", "", mentioning(OUTSIDER)), room_state()
        )


class RegistrationTestSuite(unittest.TestCase):
    """The listener refuses to run on a worker.

    on_new_event fires on every process that sees the event, so only the main
    process may act on it: one add per mention.
    """

    def test_a_worker_cannot_run_the_listener(self):
        api = MagicMock()
        api.worker_name = "synapse-generic-worker"

        with self.assertRaises(AssertionError):
            MentionInviteListener(api, CONFIG, audiences=MagicMock())
