"""Batch operations for room notification preferences."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..notification_preferences import Level
from ..push_rule_manager import PushRuleManager
from .group_conversation_store import RoomMember

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class RoomBatchUpdater:
    """Batch updates notification preferences for any set of room/user pairs."""

    def __init__(
        self,
        hs: HomeServer,
        account_data_type="com.powerhrg.connect.notification_preference",
    ):
        self.hs = hs
        self.account_data_type = account_data_type

    async def update_room_members_to_level(
        self,
        target_level: Level,
        room_members: list[RoomMember],
        dry_run: bool = True,
    ) -> None:
        """Set notification preference to the target Level for each room member via PushRuleManager.

        Usage:
            updater = RoomBatchUpdater(hs)
            members = [RoomMember(user_name="@alice:hs", room_id="!123:hs"), RoomMember(user_name="@bob:hs", room_id="!456:hs")]
            await updater.update_room_members_to_level(Level.EVERY_MESSAGE, members, dry_run=False)

        Args:
            target_level: The notification Level to set for each member.
            room_members: List of RoomMember(user_name, room_id) to update.
            dry_run: If True, log what would change without applying. Defaults to True.
        """
        if not room_members:
            logger.warning("No room members to update.")
            return

        logger.info(
            f"{len(room_members)} room members to update to {target_level.value}"
        )

        if dry_run:
            logger.info(
                f"Dry run: {len(room_members)} room members would be updated to {target_level.value}"
            )
            return

        logger.info(
            f"Updating {len(room_members)} room members to {target_level.value}"
        )

        for room_member in room_members:
            push_rule_manager = PushRuleManager(
                self.hs,
                room_member.user_name,
                room_member.room_id,
                self.account_data_type,
            )
            await push_rule_manager.update_to_level(target_level)
