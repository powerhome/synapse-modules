"""Tests for archive rooms store."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from connect.archive_rooms.store import ArchiveRoomStore


class ArchiveRoomStoreIsArchivedTestSuite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.main_store = MagicMock()
        self.store = ArchiveRoomStore(self.main_store)

    async def test_returns_true_when_room_is_blocked(self):
        self.main_store.is_room_blocked = AsyncMock(return_value=True)
        result = await self.store.is_archived("!room1:server")
        self.assertTrue(result)

    async def test_returns_false_when_room_is_not_blocked(self):
        self.main_store.is_room_blocked = AsyncMock(return_value=False)
        result = await self.store.is_archived("!room1:server")
        self.assertFalse(result)

    async def test_returns_false_when_room_blocked_returns_none(self):
        self.main_store.is_room_blocked = AsyncMock(return_value=None)
        result = await self.store.is_archived("!room1:server")
        self.assertFalse(result)


class GetRoomsEligibleForAutoArchiveTestSuite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.main_store = MagicMock()
        self.store = ArchiveRoomStore(self.main_store)

    async def test_returns_room_ids_from_db(self):
        self.main_store.db_pool.runInteraction = AsyncMock(
            return_value=["!room1:server", "!room2:server"]
        )
        result = await self.store.get_rooms_eligible_for_auto_archive(cutoff_ts_ms=9999)
        self.assertEqual(result, ["!room1:server", "!room2:server"])

    async def test_returns_empty_list_when_no_eligible_rooms(self):
        self.main_store.db_pool.runInteraction = AsyncMock(return_value=[])
        result = await self.store.get_rooms_eligible_for_auto_archive(cutoff_ts_ms=9999)
        self.assertEqual(result, [])

    async def test_calls_db_with_correct_interaction_name(self):
        self.main_store.db_pool.runInteraction = AsyncMock(return_value=[])
        await self.store.get_rooms_eligible_for_auto_archive(cutoff_ts_ms=9999)
        self.main_store.db_pool.runInteraction.assert_called_once_with(
            "get_rooms_eligible_for_auto_archive", unittest.mock.ANY
        )


if __name__ == "__main__":
    unittest.main()
