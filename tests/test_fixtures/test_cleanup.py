"""Tests for run and global teardown."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from fixtures.cleanup import Cleanup, RoomCleanup


def make_cleanup(marked=None, prefixed=None):
    # ``marked`` is the (mxid, owning_run_id) rows the marker SELECT returns;
    # ``prefixed`` is the mxids the localpart-prefix SELECT returns. Dispatch on
    # the interaction name so cleanup's two queries get the right result.
    marked = marked or []
    prefixed = prefixed or []

    async def run_interaction(name, *args, **kwargs):
        if name == "fixtures_select_marked_users":
            return marked
        return prefixed

    hs = MagicMock()
    hs.hostname = "localhost"
    store = MagicMock()
    store.db_pool.runInteraction = AsyncMock(side_effect=run_interaction)
    store.db_pool.simple_delete = AsyncMock(return_value=1)
    store.is_user_erased = AsyncMock(return_value=False)
    hs.get_datastores.return_value.main = store
    deactivate_handler = MagicMock()
    deactivate_handler.deactivate_account = AsyncMock()
    hs.get_deactivate_account_handler.return_value = deactivate_handler
    return Cleanup(hs), deactivate_handler


def external_id_unlinks(cleanup):
    # The mxids whose `user_external_ids` rows were deleted.
    calls = cleanup.store.db_pool.simple_delete.await_args_list
    assert all(c.kwargs["table"] == "user_external_ids" for c in calls)
    return [c.kwargs["keyvalues"]["user_id"] for c in calls]


class CleanupExternalIdsTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_deactivating_users_unlinks_their_external_ids(self):
        cleanup, _ = make_cleanup()

        await cleanup.deactivate(["@alice:localhost", "@bob.smith:localhost"])

        # Deactivation keeps the `user_external_ids` row, which would otherwise
        # block a later user from claiming the same external id.
        self.assertEqual(
            external_id_unlinks(cleanup), ["@alice:localhost", "@bob.smith:localhost"]
        )

    async def test_unlinks_after_the_account_is_deactivated(self):
        cleanup, deactivate_handler = make_cleanup()
        calls = []
        deactivate_handler.deactivate_account = AsyncMock(
            side_effect=lambda *a, **k: calls.append("deactivate")
        )
        cleanup.store.db_pool.simple_delete = AsyncMock(
            side_effect=lambda **k: calls.append("unlink")
        )

        await cleanup.deactivate(["@alice:localhost"])

        self.assertEqual(calls, ["deactivate", "unlink"])


class CleanupProfilesTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_deactivating_users_removes_their_connect_profiles(self):
        profiles = MagicMock()
        profiles.remove = AsyncMock()
        cleanup, _ = make_cleanup()
        cleanup.profiles = profiles

        await cleanup.deactivate(["@alice:localhost", "@bob.smith:localhost"])

        profiles.remove.assert_awaited_once_with(["alice", "bob.smith"])

    async def test_works_without_a_profile_writer(self):
        cleanup, deactivate_handler = make_cleanup()

        count = await cleanup.deactivate(["@alice:localhost"])

        self.assertEqual(count, 1)
        deactivate_handler.deactivate_account.assert_awaited_once()


class CleanupRunTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_deactivates_users_owned_by_the_run_by_marker(self):
        cleanup, deactivate_handler = make_cleanup(
            marked=[("@andrew.kloecker:localhost", "cypress-1"), ("@x:localhost", "y")]
        )

        count = await cleanup.cleanup_run("cypress-1", "fx_abc_")

        self.assertEqual(count, 1)
        deactivate_handler.deactivate_account.assert_awaited_once()
        first = deactivate_handler.deactivate_account.await_args_list[0]
        self.assertEqual(first.args[0], "@andrew.kloecker:localhost")
        self.assertTrue(first.kwargs["erase_data"])

    async def test_falls_back_to_the_localpart_prefix(self):
        cleanup, deactivate_handler = make_cleanup(
            prefixed=["@fx_abc_1:localhost", "@fx_abc_2:localhost"]
        )

        count = await cleanup.cleanup_run("run-1", "fx_abc_")

        self.assertEqual(count, 2)

    async def test_dedupes_a_user_matched_by_both_marker_and_prefix(self):
        cleanup, deactivate_handler = make_cleanup(
            marked=[("@fx_abc_1:localhost", "run-1")],
            prefixed=["@fx_abc_1:localhost"],
        )

        count = await cleanup.cleanup_run("run-1", "fx_abc_")

        self.assertEqual(count, 1)

    async def test_no_matches_deactivates_nothing(self):
        cleanup, deactivate_handler = make_cleanup()

        count = await cleanup.cleanup_run("run-1", "fx_abc_")

        self.assertEqual(count, 0)
        deactivate_handler.deactivate_account.assert_not_awaited()


class CleanupInventoryTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_returns_active_marked_users_with_their_owning_run(self):
        cleanup, _ = make_cleanup(
            marked=[("@a:localhost", "run-1"), ("@b:localhost", "run-2")]
        )

        result = await cleanup.inventory()

        self.assertEqual(result, [("@a:localhost", "run-1"), ("@b:localhost", "run-2")])

    async def test_is_empty_when_no_users_are_marked(self):
        cleanup, _ = make_cleanup()

        result = await cleanup.inventory()

        self.assertEqual(result, [])


class CleanupAllTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_deactivates_every_marked_user_across_runs(self):
        cleanup, deactivate_handler = make_cleanup(
            marked=[("@a:localhost", "run-1"), ("@b:localhost", "run-2")]
        )

        count = await cleanup.cleanup_all()

        self.assertEqual(count, 2)
        self.assertEqual(deactivate_handler.deactivate_account.await_count, 2)
        first = deactivate_handler.deactivate_account.await_args_list[0]
        self.assertTrue(first.kwargs["erase_data"])

    async def test_no_fixtures_users_deactivates_nothing(self):
        cleanup, deactivate_handler = make_cleanup()

        count = await cleanup.cleanup_all()

        self.assertEqual(count, 0)
        deactivate_handler.deactivate_account.assert_not_awaited()


def make_room_cleanup(room_ids):
    hs = MagicMock()
    store = MagicMock()
    store.db_pool.runInteraction = AsyncMock(return_value=room_ids)
    store.unblock_room = AsyncMock()
    store.db_pool.simple_delete = AsyncMock()
    hs.get_datastores.return_value.main = store
    pagination_handler = MagicMock()
    pagination_handler.purge_room = AsyncMock()
    hs.get_pagination_handler.return_value = pagination_handler
    return RoomCleanup(hs), pagination_handler


class RoomCleanupTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_purging_a_room_also_unblocks_it(self):
        cleanup, pagination_handler = make_room_cleanup(["!a:localhost"])
        order = []
        pagination_handler.purge_room = AsyncMock(
            side_effect=lambda *a, **k: order.append("purge")
        )
        cleanup.store.unblock_room = AsyncMock(
            side_effect=lambda room_id: order.append(f"unblock {room_id}")
        )

        await cleanup.purge(["!a:localhost"])

        # An archived room is blocked; purge leaves the `blocked_rooms` row
        # behind, so it is cleared once the room is gone.
        self.assertEqual(order, ["purge", "unblock !a:localhost"])

    async def test_purging_a_room_deletes_its_people_conversation(self):
        cleanup, _ = make_room_cleanup(["!a:localhost"])

        await cleanup.purge(["!a:localhost"])

        cleanup.store.db_pool.simple_delete.assert_awaited_once_with(
            table="connect.people_conversations",
            keyvalues={"room_id": "!a:localhost"},
            desc="fixtures_delete_people_conversation",
        )

    async def test_cleanup_run_purges_each_room_owned_by_the_run(self):
        cleanup, pagination_handler = make_room_cleanup(
            ["!a:localhost", "!b:localhost"]
        )

        count = await cleanup.cleanup_run("run-1")

        self.assertEqual(count, 2)
        self.assertEqual(pagination_handler.purge_room.await_count, 2)
        first = pagination_handler.purge_room.await_args_list[0]
        self.assertEqual(first.args[0], "!a:localhost")
        self.assertTrue(first.kwargs["force"])

    async def test_cleanup_all_purges_every_marked_room(self):
        cleanup, pagination_handler = make_room_cleanup(["!a:localhost"])

        count = await cleanup.cleanup_all()

        self.assertEqual(count, 1)
        pagination_handler.purge_room.assert_awaited_once()

    async def test_no_rooms_purges_nothing(self):
        cleanup, pagination_handler = make_room_cleanup([])

        count = await cleanup.cleanup_all()

        self.assertEqual(count, 0)
        pagination_handler.purge_room.assert_not_awaited()


class RoomInventoryTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_returns_each_room_with_its_owning_run(self):
        cleanup, _ = make_room_cleanup(
            [("!a:localhost", "run-1"), ("!b:localhost", "run-2")]
        )

        result = await cleanup.inventory()

        self.assertEqual(result, [("!a:localhost", "run-1"), ("!b:localhost", "run-2")])

    async def test_is_empty_when_no_rooms_are_marked(self):
        cleanup, _ = make_room_cleanup([])

        result = await cleanup.inventory()

        self.assertEqual(result, [])


class DeactivateIdempotenceTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_skips_users_already_erased(self):
        cleanup, deactivate_handler = make_cleanup()
        cleanup.store.is_user_erased = AsyncMock(
            side_effect=lambda mxid: mxid == "@done:hs"
        )

        count = await cleanup.deactivate(["@done:hs", "@live:hs"])

        self.assertEqual(count, 1)
        deactivate_handler.deactivate_account.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
