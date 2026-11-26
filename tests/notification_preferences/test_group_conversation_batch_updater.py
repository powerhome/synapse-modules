"""Tests for GroupConversationBatchUpdater."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connect.notification_preferences.batch_update import GroupConversationBatchUpdater
from connect.notification_preferences.batch_update.group_conversation_store import (
    RoomMember,
)
from connect.notification_preferences.notification_preferences import Level

user_id = "@alice:powerhrg.com"
room_id = "!room1:powerhrg.com"


def mock_hs():
    hs = MagicMock()
    db_pool = MagicMock()
    db_pool.runInteraction = AsyncMock(return_value=[])
    hs.get_datastores.return_value.main.db_pool = db_pool
    return hs


class GroupConversationBatchUpdaterTestSuite(unittest.IsolatedAsyncioTestCase):
    @patch(
        "connect.notification_preferences.batch_update.group_conversation.RoomBatchUpdater"
    )
    async def test__update_members_without_push_rules__fetches_and_delegates(
        self, mock_room_updater_cls
    ):
        hs = mock_hs()
        store_entries = [RoomMember(room_id=room_id, user_name=user_id)]
        hs.get_datastores.return_value.main.db_pool.runInteraction = AsyncMock(
            return_value=store_entries
        )
        mock_room_updater_cls.return_value.update_room_members_to_level = AsyncMock()

        updater = GroupConversationBatchUpdater(hs)
        await updater.update_members_without_push_rules(dry_run=False)

        mock_room_updater_cls.return_value.update_room_members_to_level.assert_called_once_with(
            Level.EVERY_MESSAGE, store_entries, False
        )

    @patch(
        "connect.notification_preferences.batch_update.group_conversation.RoomBatchUpdater"
    )
    async def test__update_members_without_push_rules__dry_run(
        self, mock_room_updater_cls
    ):
        hs = mock_hs()
        store_entries = [RoomMember(room_id=room_id, user_name=user_id)]
        hs.get_datastores.return_value.main.db_pool.runInteraction = AsyncMock(
            return_value=store_entries
        )
        mock_room_updater_cls.return_value.update_room_members_to_level = AsyncMock()

        updater = GroupConversationBatchUpdater(hs)
        await updater.update_members_without_push_rules(dry_run=True)

        mock_room_updater_cls.return_value.update_room_members_to_level.assert_called_once_with(
            Level.EVERY_MESSAGE, store_entries, True
        )


if __name__ == "__main__":
    unittest.main()
