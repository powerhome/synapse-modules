"""Batch operations for group conversation notification preferences."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..notification_preferences import Level
from .group_conversation_store import GroupConversationStore
from .room import RoomBatchUpdater

if TYPE_CHECKING:
    from synapse.server import HomeServer


class GroupConversationBatchUpdater:
    """Sets notification preferences for group conversation members without push rules."""

    def __init__(
        self,
        hs: HomeServer,
        account_data_type="com.powerhrg.connect.notification_preference",
    ):
        self.store = GroupConversationStore(hs.get_datastores().main.db_pool)
        self.room_batch_updater = RoomBatchUpdater(hs, account_data_type)

    async def update_members_without_push_rules(self, dry_run: bool = True) -> None:
        """Set EVERY_MESSAGE notification level for group conversation members lacking push rules.

        Usage:
            updater = GroupConversationBatchUpdater(hs)
            await updater.update_members_without_push_rules(dry_run=False)

        Args:
            dry_run: If True, log what would change without applying. Defaults to True.
        """
        room_members = await self.store.get_members_without_push_rules()
        await self.room_batch_updater.update_room_members_to_level(
            Level.EVERY_MESSAGE, room_members, dry_run
        )
