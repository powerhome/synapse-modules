"""Tests for the read-receipt provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.constants import ReceiptTypes
from synapse.api.errors import SynapseError
from synapse.types import UserID

from fixtures.receipts import ReceiptProvisioner

SECRET = "s3cret"


def make_provisioner(instance_name="master", writers=("master",), instance_map=None):
    hs = MagicMock()
    hs.get_instance_name.return_value = instance_name
    hs.config.worker.writers.receipts = list(writers)
    hs.config.worker.instance_map = instance_map or {}
    receipts_handler = MagicMock()
    receipts_handler.received_client_receipt = AsyncMock()
    hs.get_receipts_handler.return_value = receipts_handler
    http = MagicMock()
    http.post_json_get_json = AsyncMock()
    hs.get_simple_http_client.return_value = http
    return ReceiptProvisioner(hs, SECRET), receipts_handler, http


USERS = {"alice": {"mxid": "@alice:localhost"}, "bob": {"mxid": "@bob:localhost"}}
ROOMS = {"r1": "!room:localhost"}
EVENTS = {"e1": "$first:localhost"}


class ProvisionTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_records_locally_when_this_instance_is_the_receipts_writer(self):
        provisioner, handler, http = make_provisioner()

        await provisioner.provision(
            {"room": "r1", "user": "alice", "event": "e1"}, USERS, ROOMS, EVENTS
        )

        handler.received_client_receipt.assert_awaited_once_with(
            room_id="!room:localhost",
            receipt_type=ReceiptTypes.READ,
            user_id=UserID.from_string("@alice:localhost"),
            event_id="$first:localhost",
            thread_id=None,
        )
        http.post_json_get_json.assert_not_awaited()

    async def test_forwards_to_the_receipts_writer_when_it_is_another_instance(self):
        location = MagicMock()
        location.scheme.return_value = "http"
        location.netloc.return_value = "synapse-receipts-worker:9193"
        provisioner, handler, http = make_provisioner(
            instance_name="master",
            writers=("synapse-receipts-worker",),
            instance_map={"synapse-receipts-worker": location},
        )

        await provisioner.provision(
            {"room": "r1", "user": "alice", "event": "e1"}, USERS, ROOMS, EVENTS
        )

        handler.received_client_receipt.assert_not_awaited()
        http.post_json_get_json.assert_awaited_once_with(
            "http://synapse-receipts-worker:9193/_fixtures/receipt",
            {
                "room_id": "!room:localhost",
                "user_id": "@alice:localhost",
                "event_id": "$first:localhost",
            },
            headers={b"Authorization": [b"Bearer " + SECRET.encode("ascii")]},
        )

    async def test_invalid_specs_are_bad_requests(self):
        cases = {
            "missing_room": {"user": "alice", "event": "e1"},
            "unknown_user_ref": {"room": "r1", "user": "ghost", "event": "e1"},
            "missing_event": {"room": "r1", "user": "alice"},
            "unknown_event_ref": {"room": "r1", "user": "alice", "event": "ghost"},
        }
        for name, spec in cases.items():
            with self.subTest(name):
                provisioner, _, _ = make_provisioner()
                with self.assertRaises(SynapseError) as ctx:
                    await provisioner.provision(spec, USERS, ROOMS, EVENTS)
                self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
