"""Tests for the room provisioner."""

import json
import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import SynapseError

from fixtures.markers import CREATION_MARKER, RUN_MARKER
from fixtures.rooms import RoomProvisioner


def make_provisioner(room_id="!room:localhost"):
    hs = MagicMock()
    creation_handler = MagicMock()
    creation_handler.create_room = AsyncMock(return_value=(room_id, None, 1))
    hs.get_room_creation_handler.return_value = creation_handler
    return RoomProvisioner(hs), creation_handler


USERS = {"alice": {"mxid": "@alice:localhost"}, "bob": {"mxid": "@bob:localhost"}}


class ProvisionTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_marks_the_create_event_with_the_deployment_and_ref(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice"}, USERS, "run-1", "dep-1"
        )

        _, config = creation_handler.create_room.await_args.args
        self.assertEqual(
            config["creation_content"],
            {CREATION_MARKER: {"deployment_id": "dep-1", "ref": "r1"}},
        )

    async def test_creates_the_room_as_the_creator_and_returns_its_id(self):
        provisioner, creation_handler = make_provisioner()

        result = await provisioner.provision(
            {"ref": "r1", "creator": "alice"}, USERS, "run-1", "dep-1"
        )

        requester, config = creation_handler.create_room.await_args.args
        self.assertEqual(requester.user.to_string(), "@alice:localhost")
        self.assertEqual(result, {"ref": "r1", "room_id": "!room:localhost"})

    async def test_stamps_the_run_marker_state_event_keyed_by_run_id(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice"}, USERS, "run-1", "dep-1"
        )

        config = creation_handler.create_room.await_args.args[1]
        self.assertEqual(
            config["initial_state"],
            [
                {
                    "type": RUN_MARKER,
                    "state_key": "run-1",
                    "content": {"run_id": "run-1", "deployment_id": "dep-1"},
                }
            ],
        )

    async def test_public_kind_uses_the_public_preset(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice", "kind": "public"}, USERS, "run-1", "dep-1"
        )

        self.assertEqual(
            creation_handler.create_room.await_args.args[1]["preset"], "public_chat"
        )

    async def test_defaults_to_the_private_preset(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice"}, USERS, "run-1", "dep-1"
        )

        self.assertEqual(
            creation_handler.create_room.await_args.args[1]["preset"], "private_chat"
        )

    async def test_dm_kind_creates_a_private_direct_room(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice", "kind": "dm"}, USERS, "run-1", "dep-1"
        )

        config = creation_handler.create_room.await_args.args[1]
        self.assertEqual(config["preset"], "private_chat")
        self.assertEqual(config["visibility"], "private")
        self.assertIs(config["is_direct"], True)

    async def test_dm_invites_its_members_at_creation_so_connect_registers_it(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {
                "ref": "r1",
                "creator": "alice",
                "kind": "dm",
                "members": ["alice", "bob", "bob"],
            },
            USERS,
            "run-1",
            "dep-1",
        )

        config = creation_handler.create_room.await_args.args[1]
        self.assertEqual(config["invite"], ["@bob:localhost"])

    async def test_non_dm_rooms_do_not_claim_to_be_direct(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice", "kind": "public"}, USERS, "run-1", "dep-1"
        )

        config = creation_handler.create_room.await_args.args[1]
        self.assertNotIn("is_direct", config)
        self.assertNotIn("invite", config)

    async def test_sets_the_topic_when_given(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice", "topic": "Q4 planning"},
            USERS,
            "run-1",
            "dep-1",
        )

        config = creation_handler.create_room.await_args.args[1]
        self.assertEqual(config["topic"], "Q4 planning")

    async def test_omits_the_topic_when_absent(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice"}, USERS, "run-1", "dep-1"
        )

        config = creation_handler.create_room.await_args.args[1]
        self.assertNotIn("topic", config)

    async def test_admins_get_power_level_100_alongside_the_creator(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice", "admins": ["bob"]},
            USERS,
            "run-1",
            "dep-1",
        )

        config = creation_handler.create_room.await_args.args[1]
        # Synapse replaces `users` wholesale when overridden, so the creator
        # must be listed too or they would lose their default 100.
        self.assertEqual(
            config["power_level_content_override"],
            {"users": {"@alice:localhost": 100, "@bob:localhost": 100}},
        )

    async def test_sets_no_power_level_override_without_admins(self):
        provisioner, creation_handler = make_provisioner()

        await provisioner.provision(
            {"ref": "r1", "creator": "alice"}, USERS, "run-1", "dep-1"
        )

        config = creation_handler.create_room.await_args.args[1]
        self.assertNotIn("power_level_content_override", config)

    async def test_unknown_admin_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "r1", "creator": "alice", "admins": ["ghost"]},
                USERS,
                "run-1",
                "dep-1",
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_missing_creator_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision({"ref": "r1"}, USERS, "run-1", "dep-1")
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_unknown_creator_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "r1", "creator": "nobody"}, USERS, "run-1", "dep-1"
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


class CreatedByTestSuite(unittest.IsolatedAsyncioTestCase):
    def test_matches_only_rooms_marked_for_this_deployment_and_ref(self):
        txn = MagicMock()
        txn.__iter__.return_value = iter(
            [
                ("!mine:hs", _create_json({"deployment_id": "dep-1", "ref": "r1"})),
                ("!other:hs", _create_json({"deployment_id": "dep-2", "ref": "r1"})),
                ("!plain:hs", '{"content": {"creator": "@a:hs"}}'),
            ]
        )

        found = RoomProvisioner._created_by_txn(
            txn, "@a:hs", {"deployment_id": "dep-1", "ref": "r1"}
        )

        self.assertEqual(found, ["!mine:hs"])


def _create_json(marker):
    return json.dumps({"content": {CREATION_MARKER: marker}})


if __name__ == "__main__":
    unittest.main()
