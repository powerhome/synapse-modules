"""Tests for the reaction provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.constants import EventTypes, RelationTypes
from synapse.api.errors import SynapseError

from fixtures.reactions import ReactionProvisioner


def make_provisioner(event_id="$reaction:localhost"):
    hs = MagicMock()
    creation_handler = MagicMock()
    event = MagicMock()
    event.event_id = event_id
    creation_handler.create_and_send_nonmember_event = AsyncMock(
        return_value=(event, 1)
    )
    hs.get_event_creation_handler.return_value = creation_handler
    return ReactionProvisioner(hs), creation_handler


USERS = {"alice": {"mxid": "@alice:localhost"}, "bob": {"mxid": "@bob:localhost"}}
ROOMS = {"r1": "!room:localhost"}
EVENTS = {"m1": "$message:localhost"}


class ProvisionTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_annotates_the_target_event_as_the_sender(self):
        provisioner, handler = make_provisioner()

        await provisioner.provision(
            {"room": "r1", "sender": "bob", "event": "m1", "key": "👍"},
            USERS,
            ROOMS,
            EVENTS,
            txn_id="txn",
        )

        requester, event_dict = handler.create_and_send_nonmember_event.await_args.args
        self.assertEqual(requester.user.to_string(), "@bob:localhost")
        self.assertEqual(event_dict["type"], EventTypes.Reaction)
        self.assertEqual(event_dict["room_id"], "!room:localhost")
        self.assertEqual(event_dict["sender"], "@bob:localhost")
        self.assertEqual(
            event_dict["content"],
            {
                "m.relates_to": {
                    "rel_type": RelationTypes.ANNOTATION,
                    "event_id": "$message:localhost",
                    "key": "👍",
                }
            },
        )

    async def test_returns_the_reaction_event_id(self):
        provisioner, _ = make_provisioner(event_id="$r1:localhost")

        result = await provisioner.provision(
            {"room": "r1", "sender": "bob", "event": "m1", "key": "👍"},
            USERS,
            ROOMS,
            EVENTS,
            txn_id="txn",
        )

        self.assertEqual(result, {"event_id": "$r1:localhost"})

    async def test_pins_origin_server_ts_when_ts_is_given(self):
        provisioner, handler = make_provisioner()

        await provisioner.provision(
            {
                "room": "r1",
                "sender": "bob",
                "event": "m1",
                "key": "👍",
                "ts": 1700000000000,
            },
            USERS,
            ROOMS,
            EVENTS,
            txn_id="txn",
        )

        event_dict = handler.create_and_send_nonmember_event.await_args.args[1]
        self.assertEqual(event_dict["origin_server_ts"], 1700000000000)

    async def test_does_not_ratelimit_seeded_reactions(self):
        provisioner, handler = make_provisioner()

        await provisioner.provision(
            {"room": "r1", "sender": "bob", "event": "m1", "key": "👍"},
            USERS,
            ROOMS,
            EVENTS,
            txn_id="txn",
        )

        self.assertFalse(
            handler.create_and_send_nonmember_event.await_args.kwargs["ratelimit"]
        )

    async def test_missing_key_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "bob", "event": "m1"},
                USERS,
                ROOMS,
                EVENTS,
                txn_id="txn",
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_unknown_event_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "bob", "event": "ghost", "key": "👍"},
                USERS,
                ROOMS,
                EVENTS,
                txn_id="txn",
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_unknown_room_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "ghost", "sender": "bob", "event": "m1", "key": "👍"},
                USERS,
                ROOMS,
                EVENTS,
                txn_id="txn",
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_unknown_sender_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "ghost", "event": "m1", "key": "👍"},
                USERS,
                ROOMS,
                EVENTS,
                txn_id="txn",
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
