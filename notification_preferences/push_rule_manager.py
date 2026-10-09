"""This module is responsible for managing push rules for a user in a room."""

import logging
from typing import TYPE_CHECKING

from synapse.util.async_helpers import Linearizer

from .account_data_handler import AccountDataHandler
from .model import ConnectPushRule
from .notification_preferences import (
    AllAndMePreference,
    EveryMessagePreference,
    JustMePreference,
    Level,
    NoNotificationsPreference,
    NotificationPreference,
)
from .store import NotificationPreferenceStore

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class PushRuleManager:
    """A class that manages push rules for a user in a room."""

    def __init__(
        self,
        hs: "HomeServer",
        user_id: str,
        room_id: str,
        notification_preference_event_type: str,
    ):
        self.hs = hs
        self.user_id = user_id
        self.room_id = room_id
        self.notification_preference_event_type = notification_preference_event_type
        self.api = hs.get_module_api()
        self.main_store = hs.get_datastores().main
        self.store = NotificationPreferenceStore(
            self.main_store.db_pool, notification_preference_event_type
        )
        self._push_rules_handler = hs.get_push_rules_handler()
        self._push_rule_linearizer = Linearizer(name="_push_rule_linearizer")

    def get_preference_from_level(self, level: Level) -> NotificationPreference:
        """
        Get the notification preference object for the given level.

        Parameters:
            level (Level): The level for which to get the notification preference object.

        Returns:
            NotificationPreference: The notification preference object for the level.
        """
        if level == Level.EVERY_MESSAGE:
            return EveryMessagePreference(self.user_id, self.room_id)
        elif level == Level.ALL_AND_ME:
            return AllAndMePreference(self.user_id, self.room_id)
        elif level == Level.JUST_ME:
            return JustMePreference(self.user_id, self.room_id)
        else:
            return NoNotificationsPreference(self.user_id, self.room_id)

    def push_rules(self, level: Level) -> list[ConnectPushRule]:
        preference = self.get_preference_from_level(level)
        return preference.get_push_rules()

    async def update_to_level(self, desired_level: Level) -> None:
        desired_push_rules = self.push_rules(desired_level)
        current_push_rules = await self.store.get_by_room_id(self.room_id, self.user_id)

        desired_rule_ids = []
        for rule in desired_push_rules:
            desired_rule_ids.append(rule.rule_id)

        for current_rule in current_push_rules:
            desired_rule = next(
                (
                    rule
                    for rule in desired_push_rules
                    if rule.rule_id == current_rule.rule_id
                ),
                None,
            )
            if desired_rule:
                await self.toggle_rule(current_rule, desired_rule.enabled)
            else:
                await self.toggle_rule(current_rule, False)

        for desired_rule in desired_push_rules:
            await self.add_rule(desired_rule)

        account_data_handler = AccountDataHandler(
            self.hs,
            user_id=self.user_id,
            room_id=self.room_id,
            notification_preference_event_type=self.notification_preference_event_type,
        )
        await account_data_handler.update_notification_preference(desired_level)

    async def toggle_rule(self, rule: ConnectPushRule, should_be_enabled: bool) -> None:
        try:
            await self.main_store.set_push_rule_enabled(
                self.user_id, rule.rule_id, should_be_enabled, False
            )
        except Exception as e:
            logger.error("Error updating push rule: %s", e)

    async def add_rule(self, rule: ConnectPushRule) -> None:
        async with self._push_rule_linearizer.queue(self.user_id):
            try:
                await self.main_store.add_push_rule(
                    user_id=self.user_id,
                    rule_id=rule.rule_id,
                    priority_class=rule.priority_class,
                    conditions=rule.conditions,
                    actions=rule.actions,
                    before=None,
                    after=None,
                )
                self._push_rules_handler.notify_user(self.user_id)
            except Exception as e:
                logger.error("Error creating push rule: %s", e)
