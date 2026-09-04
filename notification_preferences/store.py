"""Notification Preferences Store."""

import json
import logging
from typing import Optional

from synapse.storage.database import DatabasePool, LoggingTransaction

from .model import ConnectPushRule
from .notification_preferences import Level

logger = logging.getLogger(__name__)


class NotificationPreferenceStore:
    """A class that handles notification preference database queries."""

    def __init__(
        self,
        db_pool: DatabasePool,
        notification_preference_event_type: str,
    ):
        self.db_pool = db_pool
        self.notification_preference_event_type = notification_preference_event_type

    async def get_by_room_id(self, room_id: str, user_id: str) -> list[ConnectPushRule]:
        def select(txn: LoggingTransaction):
            sql = (
                "SELECT p.id, p.user_name, p.rule_id, p.priority_class, p.priority, p.conditions, p.actions, e.enabled "
                "FROM public.push_rules AS p "
                "LEFT JOIN public.push_rules_enable AS e "
                "ON p.user_name = e.user_name AND p.rule_id = e.rule_id "
                "WHERE p.user_name = ? AND p.rule_id::TEXT LIKE ?"
            )
            txn.execute(sql, (user_id, f"%{room_id}%"))
            results = []
            model_attributes = [
                "id",
                "user_name",
                "rule_id",
                "priority_class",
                "priority",
                "conditions",
                "actions",
                "enabled",
            ]
            for row in txn:
                row_as_dict = dict(zip(model_attributes, row))
                results.append(row_as_dict)
            return results

        preferences = await self.db_pool.runInteraction(
            "get_by_room_id", select, db_autocommit=True
        )

        models = []
        for preference in preferences:
            model = ConnectPushRule(**preference)
            models.append(model)

        return models

    async def get_level_from_account_data(
        self, room_id: str, user_id: str
    ) -> Optional[Level]:
        """Get the current notification level from room account data.

        Args:
            room_id: The room ID to get the level for
            user_id: The user ID to get the level for

        Returns:
            Optional[Level]: The current notification level if set, None otherwise
        """

        def select(txn: LoggingTransaction):
            sql = (
                "SELECT content "
                "FROM public.room_account_data "
                "WHERE user_id = ? AND room_id = ? AND account_data_type = ?"
            )
            txn.execute(
                sql,
                (
                    user_id,
                    room_id,
                    self.notification_preference_event_type,
                ),
            )
            row = txn.fetchone()
            if row:
                return row[0]
            return None

        result = await self.db_pool.runInteraction(
            "get_current_level_from_room_account_data", select, db_autocommit=True
        )

        if result:
            json_result = json.loads(result)
            return Level(json_result["level"])
        return None
