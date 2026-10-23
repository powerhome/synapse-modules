"""Tests for the event provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.constants import EventTypes
from synapse.api.errors import SynapseError

from fixtures.events import EventProvisioner


def make_provisioner(event_id="$evt:localhost"):
    hs = MagicMock()
    creation_handler = MagicMock()
    event = MagicMock()
    event.event_id = event_id
    creation_handler.create_and_send_nonmember_event = AsyncMock(
        return_value=(event, 1)
    )
    hs.get_event_creation_handler.return_value = creation_handler
    return EventProvisioner(hs), creation_handler


USERS = {"alice": {"mxid": "@alice:localhost"}, "bob": {"mxid": "@bob:localhost"}}
ROOMS = {"r1": "!room:localhost"}


class ProvisionTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_sends_a_text_message_as_the_sender_into_the_room(self):
        provisioner, handler = make_provisioner()

        result = await provisioner.provision(
            {"ref": "e1", "room": "r1", "sender": "alice", "body": "hi"},
            USERS,
            ROOMS,
            {},
        )

        requester, event_dict = handler.create_and_send_nonmember_event.await_args.args
        self.assertEqual(requester.user.to_string(), "@alice:localhost")
        self.assertEqual(event_dict["type"], EventTypes.Message)
        self.assertEqual(event_dict["room_id"], "!room:localhost")
        self.assertEqual(event_dict["sender"], "@alice:localhost")
        self.assertEqual(event_dict["content"], {"msgtype": "m.text", "body": "hi"})
        self.assertEqual(result, {"ref": "e1", "event_id": "$evt:localhost"})

    async def test_pins_origin_server_ts_when_ts_is_given(self):
        provisioner, handler = make_provisioner()

        await provisioner.provision(
            {"room": "r1", "sender": "alice", "body": "hi", "ts": 1700000000000},
            USERS,
            ROOMS,
            {},
        )

        event_dict = handler.create_and_send_nonmember_event.await_args.args[1]
        self.assertEqual(event_dict["origin_server_ts"], 1700000000000)

    async def test_omits_origin_server_ts_when_ts_is_absent(self):
        provisioner, handler = make_provisioner()

        await provisioner.provision(
            {"room": "r1", "sender": "alice", "body": "hi"}, USERS, ROOMS, {}
        )

        event_dict = handler.create_and_send_nonmember_event.await_args.args[1]
        self.assertNotIn("origin_server_ts", event_dict)

    async def test_reply_to_relates_an_earlier_event_by_ref(self):
        provisioner, handler = make_provisioner()

        await provisioner.provision(
            {"room": "r1", "sender": "bob", "body": "re", "reply_to": "e1"},
            USERS,
            ROOMS,
            {"e1": "$first:localhost"},
        )

        event_dict = handler.create_and_send_nonmember_event.await_args.args[1]
        self.assertEqual(
            event_dict["content"]["m.relates_to"],
            {"m.in_reply_to": {"event_id": "$first:localhost"}},
        )

    async def test_does_not_ratelimit_seeded_events(self):
        provisioner, handler = make_provisioner()

        await provisioner.provision(
            {"room": "r1", "sender": "alice", "body": "hi"}, USERS, ROOMS, {}
        )

        self.assertFalse(
            handler.create_and_send_nonmember_event.await_args.kwargs["ratelimit"]
        )

    async def test_missing_room_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"sender": "alice", "body": "hi"}, USERS, ROOMS, {}
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_unknown_room_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "ghost", "sender": "alice", "body": "hi"}, USERS, ROOMS, {}
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_unknown_sender_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "ghost", "body": "hi"}, USERS, ROOMS, {}
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_missing_body_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "alice"}, USERS, ROOMS, {}
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_unknown_reply_to_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "alice", "body": "hi", "reply_to": "ghost"},
                USERS,
                ROOMS,
                {},
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_untracked_event_returns_a_none_ref(self):
        provisioner, _ = make_provisioner()

        result = await provisioner.provision(
            {"room": "r1", "sender": "alice", "body": "hi"}, USERS, ROOMS, {}
        )

        self.assertIsNone(result["ref"])


if __name__ == "__main__":
    unittest.main()
