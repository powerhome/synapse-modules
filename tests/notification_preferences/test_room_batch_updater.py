"""Tests for RoomBatchUpdater."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connect.notification_preferences.batch_update import RoomBatchUpdater
from connect.notification_preferences.batch_update.group_conversation_store import (
    RoomMember,
)
from connect.notification_preferences.notification_preferences import Level

user_id = "@alice:powerhrg.com"
other_user = "@bob:powerhrg.com"
room_id = "!room1:powerhrg.com"
room_id_2 = "!room2:powerhrg.com"


def mock_hs():
    hs = MagicMock()
    db_pool = MagicMock()
    db_pool.runInteraction = AsyncMock(return_value=[])
    hs.get_datastores.return_value.main.db_pool = db_pool
    return hs


class RoomBatchUpdaterTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test__update_room_members_to_level__no_room_members(self):
        hs = mock_hs()
        updater = RoomBatchUpdater(hs)
        await updater.update_room_members_to_level(
            Level.EVERY_MESSAGE, room_members=[], dry_run=False
        )

    @patch("connect.notification_preferences.batch_update.room.PushRuleManager")
    async def test__update_room_members_to_level__dry_run(self, mock_prm_cls):
        room_members = [RoomMember(room_id=room_id, user_name=user_id)]
        hs = mock_hs()
        updater = RoomBatchUpdater(hs)
        await updater.update_room_members_to_level(
            Level.EVERY_MESSAGE, room_members=room_members, dry_run=True
        )

        mock_prm_cls.assert_not_called()

    @patch("connect.notification_preferences.batch_update.room.PushRuleManager")
    async def test__update_room_members_to_level__executes_updates(self, mock_prm_cls):
        room_members = [
            RoomMember(room_id=room_id, user_name=user_id),
            RoomMember(room_id=room_id_2, user_name=other_user),
        ]
        hs = mock_hs()
        mock_prm_cls.return_value.update_to_level = AsyncMock()
        updater = RoomBatchUpdater(hs)
        await updater.update_room_members_to_level(
            Level.EVERY_MESSAGE, room_members=room_members, dry_run=False
        )

        self.assertEqual(mock_prm_cls.call_count, 2)
        self.assertEqual(mock_prm_cls.return_value.update_to_level.call_count, 2)

    @patch("connect.notification_preferences.batch_update.room.PushRuleManager")
    async def test__update_room_members_to_level__passes_correct_level(
        self, mock_prm_cls
    ):
        room_members = [RoomMember(room_id=room_id, user_name=user_id)]
        hs = mock_hs()
        mock_prm_cls.return_value.update_to_level = AsyncMock()
        updater = RoomBatchUpdater(hs)
        await updater.update_room_members_to_level(
            Level.EVERY_MESSAGE, room_members=room_members, dry_run=False
        )

        mock_prm_cls.return_value.update_to_level.assert_called_once_with(
            Level.EVERY_MESSAGE
        )


if __name__ == "__main__":
    unittest.main()
