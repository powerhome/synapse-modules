"""Module for Updating Account Data Related to Notification Preferences"""

import logging
from typing import TYPE_CHECKING

from .notification_preferences import Level

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class AccountDataHandler:
    """Class for updating account data related to notification preferences."""

    def __init__(
        self,
        hs: "HomeServer",
        user_id: str,
        room_id: str,
        notification_preference_event_type: str,
    ):
        self.user_id = user_id
        self.room_id = room_id
        self.notification_preference_event_type = notification_preference_event_type
        self._handler = hs.get_account_data_handler()

    async def update_notification_preference(self, level: Level) -> None:
        """
        When push rules change, this method stores the corresponding Level in room account data.

        Parameters:
            level: The notification level to be set
        """
        await self._handler.add_account_data_to_room(
            user_id=self.user_id,
            room_id=self.room_id,
            account_data_type=self.notification_preference_event_type,
            content={"level": level.value},
        )
