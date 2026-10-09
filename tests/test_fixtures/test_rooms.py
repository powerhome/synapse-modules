"""Tests for the room provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import SynapseError

from fixtures.markers import RUN_MARKER
from fixtures.rooms import RoomProvisioner


def make_provisioner(room_id="!room:localhost"):
    hs = MagicMock()
    creation_handler = MagicMock()
    creation_handler.create_room = AsyncMock(return_value=(room_id, None, 1))
    hs.get_room_creation_handler.return_value = creation_handler
    return RoomProvisioner(hs), creation_handler


USERS = {"alice": {"mxid": "@alice:localhost"}, "bob": {"mxid": "@bob:localhost"}}


class ProvisionTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_creates_the_room_as_the_creator_and_returns_its_id(self):
        provisioner, creation_handler = make_provisioner()

        result = await provisioner.provision(
            {"ref": "r1", "creator": "alice"}, USERS, "run-1"
        )

        requester, config = creation_handler.create_room.await_args.args
        self.assertEqual(requester.user.to_string(), "@alice:localhost")
        self.assertEqual(result, {"ref": "r1", "room_id": "!room:localhost"})

    async def test_stamps_the_run_marker_state_event_keyed_by_run_id(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision({"ref": "r1", "creator": "alice"}, USERS, "run-1")

        config = creation_handler.create_room.await_args.args[1]
        self.assertEqual(
            config["initial_state"],
            [
                {
                    "type": RUN_MARKER,
                    "state_key": "run-1",
                    "content": {"run_id": "run-1"},
                }
            ],
        )

    async def test_public_kind_uses_the_public_preset(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice", "kind": "public"}, USERS, "run-1"
        )

        self.assertEqual(
            creation_handler.create_room.await_args.args[1]["preset"], "public_chat"
        )

    async def test_defaults_to_the_private_preset(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision({"ref": "r1", "creator": "alice"}, USERS, "run-1")

        self.assertEqual(
            creation_handler.create_room.await_args.args[1]["preset"], "private_chat"
        )

    async def test_missing_creator_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision({"ref": "r1"}, USERS, "run-1")
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_unknown_creator_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "r1", "creator": "nobody"}, USERS, "run-1"
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
