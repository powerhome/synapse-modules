"""Module for NotificationPreferenceListener class."""

import logging
from typing import TYPE_CHECKING, Optional

from synapse.types import RoomID

from .account_data_handler import AccountDataHandler
from .bridge_client import BridgeClient
from .notification_preferences import (
    AllAndMePreference,
    ConnectPushRule,
    EveryMessagePreference,
    JustMePreference,
    Level,
    NoNotificationsPreference,
)
from .store import NotificationPreferenceStore

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class NotificationPreferenceListener:
    """Listens for changes to notification preferences."""

    def __init__(self, hs: "HomeServer", config: dict) -> None:
        self.hs = hs
        self.main_store = hs.get_datastores().main
        self.notification_preference_event_type = config[
            "notification_preference_event_type"
        ]
        self.store = NotificationPreferenceStore(
            self.main_store.db_pool, self.notification_preference_event_type
        )
        self.config = config

    async def on_push_rules_changed(self, user_id: str, room_id: RoomID) -> None:
        """
        Handles updates to notification preferences when push rules are modified.

        Parameters:
            user_id: Matrix user ID for whom the push rules are being changed
            room_id: Matrix room ID where the push rules are being changed
        """
        room_id_str = room_id.to_string()
        current_rules = await self.store.get_by_room_id(room_id_str, user_id)
        current_level = await self.store.get_level_from_account_data(
            room_id=room_id_str, user_id=user_id
        )

        new_level = self._determine_level_if_any(
            user_id, room_id_str, current_level, current_rules
        )
        if not new_level:
            return

        if current_level == new_level:
            return

        account_data_handler = AccountDataHandler(
            self.hs, user_id, room_id_str, self.notification_preference_event_type
        )
        await account_data_handler.update_notification_preference(new_level)

        if self.config.get("bridge", {}):
            bridge_client = BridgeClient(self.hs, self.config["bridge"])
            await bridge_client.update_preference(
                user_id, room_id_str, {"level": new_level.value}
            )

    def _determine_level_if_any(
        self,
        user_id: str,
        room_id: str,
        current_level: Optional[Level],
        current_rules: list[ConnectPushRule],
    ) -> Optional[Level]:
        """
        Checks if current_rules exactly matches a Level.

        Parameters:
            user_id: The user ID
            room_id: The room ID
            current_level: The current notification level
            current_rules: List of current push rules

        Returns:
            Optional[Level]: The matching notification level, or None if none match
        """
        # Get current enabled AND disabled rules
        currently_enabled_rule_ids = sorted(
            [rule.rule_id for rule in current_rules if rule.enabled]
        )
        all_current_rule_ids = sorted([rule.rule_id for rule in current_rules])

        # Get expected rules for each level
        rule_ids_for_levels = {
            Level.EVERY_MESSAGE: EveryMessagePreference(
                user_id, room_id
            ).get_push_rule_ids(),
            Level.ALL_AND_ME: AllAndMePreference(user_id, room_id).get_push_rule_ids(),
            Level.JUST_ME: JustMePreference(user_id, room_id).get_push_rule_ids(),
            Level.NO_NOTIFICATIONS: NoNotificationsPreference(
                user_id, room_id
            ).get_push_rule_ids(),
        }

        # Special case for EVERY_MESSAGE - it can be either:
        # 1) No rules at all (fresh set)
        # 2) All rules disabled (transition from another level)
        if len(currently_enabled_rule_ids) == 0:
            # If we have rules but they're all disabled, this is likely a transition to EVERY_MESSAGE
            if len(all_current_rule_ids) > 0:
                # Check if we're in an intermediate state of another transition
                transitioning_to = self._detect_transition(
                    current_level, all_current_rule_ids, rule_ids_for_levels
                )
                if transitioning_to and transitioning_to != Level.EVERY_MESSAGE:
                    return None

            # If we're not transitioning to another level, we're at EVERY_MESSAGE
            return Level.EVERY_MESSAGE

        # For other levels, check exact match with enabled rules
        for level, rule_ids in rule_ids_for_levels.items():
            if (
                level != Level.EVERY_MESSAGE
                and len(currently_enabled_rule_ids) == len(rule_ids)
                and currently_enabled_rule_ids == rule_ids
            ):
                return level

        # No level matches current rules
        return None

    def _detect_transition(
        self,
        current_level: Optional[Level],
        all_rule_ids: list[str],
        rule_ids_for_levels: dict[Level, list[str]],
    ) -> Optional[Level]:
        """
        Detects if we're in a transition to a specific level.

        Parameters:
            current_level: The current notification level
            all_rule_ids: List of all rule IDs
            rule_ids_for_levels: Dictionary mapping levels to expected rule IDs

        Returns:
            Optional[Level]: The level we're transitioning to, or None if not applicable
        """
        # Special case: if all rules are disabled and current_level is set,
        # we're likely transitioning TO EVERY_MESSAGE, not away from it
        if current_level and current_level != Level.EVERY_MESSAGE:
            return None

        # For other cases, if we have rules that look like a complex level but not all are enabled yet
        for level, expected_rule_ids in rule_ids_for_levels.items():
            # Don't consider the current level as a transition target
            if (
                level != Level.EVERY_MESSAGE
                and level != current_level
                and set(expected_rule_ids).issubset(set(all_rule_ids))
            ):
                return level

        return None
