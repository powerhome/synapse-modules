"""Tests for GroupConversationStore."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from connect.notification_preferences.batch_update.group_conversation_store import (
    GroupConversationStore,
    RoomMember,
)

room_id = "!room1:powerhrg.com"
room_id_2 = "!room2:powerhrg.com"
user_id = "@alice:powerhrg.com"
other_user = "@bob:powerhrg.com"


class GroupConversationStoreTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test__get_members_without_push_rules__returns_room_members(self):
        db_pool = MagicMock()
        rows = [(room_id, user_id), (room_id_2, other_user)]
        db_pool.runInteraction = AsyncMock(return_value=[RoomMember(*r) for r in rows])
        store = GroupConversationStore(db_pool)

        result = await store.get_members_without_push_rules()

        self.assertEqual(
            result,
            [
                RoomMember(room_id=room_id, user_name=user_id),
                RoomMember(room_id=room_id_2, user_name=other_user),
            ],
        )

    async def test__get_members_without_push_rules__returns_empty_list(self):
        db_pool = MagicMock()
        db_pool.runInteraction = AsyncMock(return_value=[])
        store = GroupConversationStore(db_pool)

        result = await store.get_members_without_push_rules()

        self.assertEqual(result, [])

    async def test__get_members_without_push_rules__calls_run_interaction(self):
        db_pool = MagicMock()
        db_pool.runInteraction = AsyncMock(return_value=[])
        store = GroupConversationStore(db_pool)

        await store.get_members_without_push_rules()

        db_pool.runInteraction.assert_called_once()
        args, kwargs = db_pool.runInteraction.call_args
        self.assertEqual(args[0], "get_members_without_push_rules")
        self.assertTrue(callable(args[1]))
        self.assertTrue(kwargs.get("db_autocommit"))

    async def test__get_members_without_push_rules__select_fn_maps_rows(self):
        db_pool = MagicMock()
        db_pool.runInteraction = AsyncMock(return_value=[])
        store = GroupConversationStore(db_pool)

        await store.get_members_without_push_rules()

        select_fn = db_pool.runInteraction.call_args[0][1]
        txn = MagicMock()
        txn.__iter__ = MagicMock(
            return_value=iter([(room_id, user_id), (room_id_2, other_user)])
        )

        result = select_fn(txn)

        txn.execute.assert_called_once()
        self.assertEqual(
            result,
            [
                RoomMember(room_id=room_id, user_name=user_id),
                RoomMember(room_id=room_id_2, user_name=other_user),
            ],
        )


if __name__ == "__main__":
    unittest.main()
