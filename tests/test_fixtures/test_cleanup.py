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
    hs.get_datastores.return_value.main = store
    deactivate_handler = MagicMock()
    deactivate_handler.deactivate_account = AsyncMock()
    hs.get_deactivate_account_handler.return_value = deactivate_handler
    return Cleanup(hs), deactivate_handler


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
    hs.get_datastores.return_value.main = store
    pagination_handler = MagicMock()
    pagination_handler.purge_room = AsyncMock()
    hs.get_pagination_handler.return_value = pagination_handler
    return RoomCleanup(hs), pagination_handler


class RoomCleanupTestSuite(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
