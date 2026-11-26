"""Tests for the redaction provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.constants import EventTypes
from synapse.api.errors import SynapseError

from fixtures.redactions import RedactionProvisioner


def make_provisioner():
    hs = MagicMock()
    creation_handler = MagicMock()
    event = MagicMock()
    event.event_id = "$redaction:localhost"
    creation_handler.create_and_send_nonmember_event = AsyncMock(
        return_value=(event, 1)
    )
    hs.get_event_creation_handler.return_value = creation_handler
    return RedactionProvisioner(hs), creation_handler


USERS = {"alice": {"mxid": "@alice:localhost"}}
ROOMS = {"r1": "!room:localhost"}
EVENTS = {"m1": "$message:localhost"}


class ProvisionTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_redacts_the_target_event_as_the_sender(self):
        provisioner, handler = make_provisioner()

        await provisioner.provision(
            {"room": "r1", "sender": "alice", "event": "m1"},
            USERS,
            ROOMS,
            EVENTS,
            txn_id="txn",
        )

        requester, event_dict = handler.create_and_send_nonmember_event.await_args.args
        self.assertEqual(requester.user.to_string(), "@alice:localhost")
        self.assertEqual(event_dict["type"], EventTypes.Redaction)
        self.assertEqual(event_dict["room_id"], "!room:localhost")
        self.assertEqual(event_dict["sender"], "@alice:localhost")
        self.assertEqual(event_dict["redacts"], "$message:localhost")
        self.assertEqual(event_dict["content"], {})

    async def test_does_not_ratelimit_seeded_redactions(self):
        provisioner, handler = make_provisioner()

        await provisioner.provision(
            {"room": "r1", "sender": "alice", "event": "m1"},
            USERS,
            ROOMS,
            EVENTS,
            txn_id="txn",
        )

        self.assertFalse(
            handler.create_and_send_nonmember_event.await_args.kwargs["ratelimit"]
        )

    async def test_unknown_event_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "sender": "alice", "event": "ghost"},
                USERS,
                ROOMS,
                EVENTS,
                txn_id="txn",
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_missing_sender_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"room": "r1", "event": "m1"}, USERS, ROOMS, EVENTS, txn_id="txn"
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
