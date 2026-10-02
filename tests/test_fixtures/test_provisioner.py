"""Tests for the fixtures orchestrator and auth."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import SynapseError

from fixtures.provisioner import Provisioner


def make_provisioner(secret="s3cret"):  # noqa: S107
    provisioner = Provisioner(MagicMock(), secret)
    provisioner.users = MagicMock()
    provisioner.rooms = MagicMock()
    provisioner.members = MagicMock()
    provisioner.members.provision = AsyncMock()
    provisioner.events = MagicMock()
    provisioner.receipts = MagicMock()
    provisioner.receipts.provision = AsyncMock()
    provisioner.media = MagicMock()
    provisioner.cleanup = MagicMock()
    provisioner.cleanup.deactivate = AsyncMock(return_value=0)
    provisioner.room_cleanup = MagicMock()
    provisioner.room_cleanup.purge = AsyncMock(return_value=0)
    return provisioner


def request_with_auth(header_value):
    request = MagicMock()
    request.getHeader.return_value = header_value
    return request


class RunPrefixTestSuite(unittest.TestCase):
    def test_is_deterministic_for_the_same_run_id(self):
        self.assertEqual(
            Provisioner.run_prefix("run-a"), Provisioner.run_prefix("run-a")
        )

    def test_differs_between_run_ids(self):
        self.assertNotEqual(
            Provisioner.run_prefix("run-a"), Provisioner.run_prefix("run-b")
        )

    def test_has_the_tagging_shape(self):
        prefix = Provisioner.run_prefix("run-a")
        self.assertTrue(prefix.startswith("fx_"))
        self.assertTrue(prefix.endswith("_"))


class AuthenticateTestSuite(unittest.TestCase):
    def test_accepts_the_matching_bearer_secret(self):
        provisioner = make_provisioner("s3cret")
        provisioner.authenticate(request_with_auth("Bearer s3cret"))

    def test_rejects_a_wrong_secret(self):
        provisioner = make_provisioner("s3cret")
        with self.assertRaises(SynapseError) as ctx:
            provisioner.authenticate(request_with_auth("Bearer nope"))
        self.assertEqual(ctx.exception.code, HTTPStatus.FORBIDDEN)

    def test_rejects_a_missing_header(self):
        provisioner = make_provisioner("s3cret")
        with self.assertRaises(SynapseError):
            provisioner.authenticate(request_with_auth(None))


class ProvisionScenarioTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_requires_a_run_id(self):
        provisioner = make_provisioner()
        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision_scenario({"users": []})
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_provisions_users_and_keys_them_by_ref(self):
        provisioner = make_provisioner()
        provisioner.users.provision = AsyncMock(
            return_value={"ref": "alice", "mxid": "@tp_x_y:hs", "localpart": "tp_x_y"}
        )

        result = await provisioner.provision_scenario(
            {"run_id": "run-a", "users": [{"ref": "alice"}]}
        )

        self.assertEqual(result["run_id"], "run-a")
        self.assertIn("alice", result["users"])

    async def test_provisions_rooms_and_keys_them_by_ref(self):
        provisioner = make_provisioner()
        provisioner.rooms.provision = AsyncMock(
            return_value={"ref": "r1", "room_id": "!r:hs"}
        )

        result = await provisioner.provision_scenario(
            {"run_id": "run-a", "rooms": [{"ref": "r1", "creator": "alice"}]}
        )

        self.assertEqual(result["rooms"], {"r1": "!r:hs"})
        provisioner.members.provision.assert_awaited_once()

    async def test_provisions_events_and_keys_them_by_ref(self):
        provisioner = make_provisioner()
        provisioner.events.provision = AsyncMock(
            return_value={"ref": "e1", "event_id": "$e:hs"}
        )

        result = await provisioner.provision_scenario(
            {"run_id": "run-a", "events": [{"ref": "e1", "room": "r1"}]}
        )

        self.assertEqual(result["events"], {"e1": "$e:hs"})

    async def test_an_untracked_event_is_not_keyed_into_the_result(self):
        provisioner = make_provisioner()
        provisioner.events.provision = AsyncMock(
            return_value={"ref": None, "event_id": "$e:hs"}
        )

        result = await provisioner.provision_scenario(
            {"run_id": "run-a", "events": [{"room": "r1"}]}
        )

        self.assertEqual(result["events"], {})

    async def test_provisions_receipts_after_events_with_the_events_map(self):
        provisioner = make_provisioner()
        provisioner.events.provision = AsyncMock(
            return_value={"ref": "e1", "event_id": "$e:hs"}
        )

        await provisioner.provision_scenario(
            {
                "run_id": "run-a",
                "events": [{"ref": "e1", "room": "r1"}],
                "receipts": [{"room": "r1", "user": "alice", "event": "e1"}],
            }
        )

        # The receipt provisioner sees the events already keyed by ref, so it
        # can resolve the event it reads up to.
        _spec, _users, _rooms, events = provisioner.receipts.provision.await_args.args
        self.assertEqual(events, {"e1": "$e:hs"})

    async def test_a_successful_scenario_rolls_nothing_back(self):
        provisioner = make_provisioner()
        provisioner.users.provision = AsyncMock(
            return_value={"ref": "alice", "mxid": "@a:hs"}
        )

        await provisioner.provision_scenario(
            {"run_id": "run-a", "users": [{"ref": "alice"}]}
        )

        provisioner.cleanup.deactivate.assert_not_awaited()
        provisioner.room_cleanup.purge.assert_not_awaited()


class RollbackTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_a_failing_room_rolls_back_the_users_already_created(self):
        provisioner = make_provisioner()
        provisioner.users.provision = AsyncMock(
            return_value={"ref": "alice", "mxid": "@a:hs"}
        )
        provisioner.rooms.provision = AsyncMock(
            side_effect=SynapseError(HTTPStatus.BAD_REQUEST, "boom")
        )

        with self.assertRaises(SynapseError):
            await provisioner.provision_scenario(
                {
                    "run_id": "run-a",
                    "users": [{"ref": "alice"}],
                    "rooms": [{"ref": "r1", "creator": "alice"}],
                }
            )

        provisioner.cleanup.deactivate.assert_awaited_once_with(["@a:hs"])
        provisioner.room_cleanup.purge.assert_awaited_once_with([])

    async def test_failing_membership_rolls_back_the_created_room_and_users(self):
        provisioner = make_provisioner()
        provisioner.users.provision = AsyncMock(
            return_value={"ref": "alice", "mxid": "@a:hs"}
        )
        provisioner.rooms.provision = AsyncMock(
            return_value={"ref": "r1", "room_id": "!r:hs"}
        )
        provisioner.members.provision = AsyncMock(
            side_effect=SynapseError(HTTPStatus.BAD_REQUEST, "no such member")
        )

        with self.assertRaises(SynapseError):
            await provisioner.provision_scenario(
                {
                    "run_id": "run-a",
                    "users": [{"ref": "alice"}],
                    "rooms": [{"ref": "r1", "creator": "alice", "members": ["ghost"]}],
                }
            )

        provisioner.room_cleanup.purge.assert_awaited_once_with(["!r:hs"])
        provisioner.cleanup.deactivate.assert_awaited_once_with(["@a:hs"])

    async def test_the_original_error_propagates_even_if_rollback_fails(self):
        provisioner = make_provisioner()
        provisioner.users.provision = AsyncMock(
            return_value={"ref": "alice", "mxid": "@a:hs"}
        )
        provisioner.events.provision = AsyncMock(
            side_effect=SynapseError(HTTPStatus.BAD_REQUEST, "boom")
        )
        provisioner.cleanup.deactivate = AsyncMock(
            side_effect=Exception("rollback boom")
        )

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision_scenario(
                {
                    "run_id": "run-a",
                    "users": [{"ref": "alice"}],
                    "events": [{"room": "r1"}],
                }
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


class InventoryTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_groups_users_and_rooms_by_owning_run(self):
        provisioner = make_provisioner()
        provisioner.cleanup.inventory = AsyncMock(
            return_value=[("@a:hs", "run-1"), ("@b:hs", "run-2")]
        )
        provisioner.room_cleanup.inventory = AsyncMock(
            return_value=[("!r:hs", "run-1")]
        )

        result = await provisioner.inventory()

        self.assertEqual(
            result,
            {
                "runs": {
                    "run-1": {"users": ["@a:hs"], "rooms": ["!r:hs"]},
                    "run-2": {"users": ["@b:hs"], "rooms": []},
                }
            },
        )

    async def test_skips_a_user_whose_owning_run_is_unknown(self):
        provisioner = make_provisioner()
        provisioner.cleanup.inventory = AsyncMock(
            return_value=[("@orphan:hs", None), ("@b:hs", "run-1")]
        )
        provisioner.room_cleanup.inventory = AsyncMock(return_value=[])

        result = await provisioner.inventory()

        self.assertEqual(result, {"runs": {"run-1": {"users": ["@b:hs"], "rooms": []}}})

    async def test_is_empty_when_nothing_is_provisioned(self):
        provisioner = make_provisioner()
        provisioner.cleanup.inventory = AsyncMock(return_value=[])
        provisioner.room_cleanup.inventory = AsyncMock(return_value=[])

        result = await provisioner.inventory()

        self.assertEqual(result, {"runs": {}})


if __name__ == "__main__":
    unittest.main()
