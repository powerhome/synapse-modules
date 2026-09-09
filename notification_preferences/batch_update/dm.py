"""Batch operations for DM notification preferences."""
from __future__ import annotations

import logging
from collections import Counter
from itertools import groupby
from operator import itemgetter
from typing import TYPE_CHECKING

from ..notification_preferences import NotificationPreferenceLookup
from .dm_store import DMBatchUpdateStore

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class DMBatchUpdater:
    """Analyzes and manages DM push rules in batch."""

    def __init__(
        self,
        hs: HomeServer,
        account_data_type="com.powerhrg.connect.notification_preference",
    ):
        self.hs = hs
        self.store = DMBatchUpdateStore(hs.get_datastores().main.db_pool)
        self.account_data_type = account_data_type

    async def analyze(self) -> dict:
        """Analyze all DM push rules and return summary statistics.

        Must be called before delete() to populate self.entries.

        Usage:
            updater = DMBatchUpdater(hs)
            stats = await updater.analyze()

        Returns:
            dict: Summary with counts of total rules, affected users, and preference types.
        """
        push_rules = await self.store.get_all_dm_push_rules()
        main_store = self.hs.get_datastores().main

        self.entries = []
        for room_id, rules_iter in groupby(push_rules, key=itemgetter("room_id")):
            rules = list(rules_iter)
            user_name = rules[0]["user_name"]
            dm_creator = rules[0]["dm_creator"]
            lookup = NotificationPreferenceLookup(user_name, room_id, rules)
            preference = lookup.find()
            account_data = await main_store.get_account_data_for_room_and_type(
                user_id=user_name,
                room_id=room_id,
                account_data_type=self.account_data_type,
            )
            self.entries.append(
                {
                    "room_id": room_id,
                    "user_name": user_name,
                    "dm_creator": dm_creator,
                    "is_creator": user_name == dm_creator,
                    "account_data": account_data,
                    "has_account_data": account_data is not None,
                    "preference_type": type(preference).__name__,
                }
            )

        type_counts = Counter(e["preference_type"] for e in self.entries)
        creator_count = sum(1 for e in self.entries if e["is_creator"])
        has_account_data_count = sum(1 for e in self.entries if e["has_account_data"])
        affected_users = {e["user_name"] for e in self.entries}

        return {
            "total_dm_push_rules": len(push_rules),
            "total_dms": len(self.entries),
            "affected_users": len(affected_users),
            "user_is_dm_creator": creator_count,
            "user_is_not_dm_creator": len(self.entries) - creator_count,
            "has_account_data": has_account_data_count,
            "missing_account_data": len(self.entries) - has_account_data_count,
            "preference_types": dict(type_counts),
        }

    async def delete(self) -> None:
        """Delete all DM push rules and notify affected users.

        Usage:
            updater = DMBatchUpdater(hs)
            await updater.delete()
        """
        push_rules = await self.store.get_all_dm_push_rules()
        if not push_rules:
            logger.warning("No DM push rules found to delete.")
            return

        logger.info(f"Deleting {len(push_rules)} push rules")

        main_store = self.hs.get_datastores().main
        push_rules_handler = self.hs.get_push_rules_handler()

        users_to_notify = set()
        for rule in push_rules:
            await main_store.delete_push_rule(rule["user_name"], rule["rule_id"])
            users_to_notify.add(rule["user_name"])

        for user_id in users_to_notify:
            push_rules_handler.notify_user(user_id)

        remaining = await self.store.get_all_dm_push_rules()
        logger.info(
            f"Deleted {len(push_rules)} push rules. Remaining: {len(remaining)}"
        )
