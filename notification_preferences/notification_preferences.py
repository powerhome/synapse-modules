"""Factory for creating push rules, based on client-side notification preference levels."""

from enum import Enum

from .model import Actions, Conditions, ConnectPushRule


class Level(Enum):
    """Connect notification levels, corresponding to client-side options."""

    EVERY_MESSAGE = "every_message"
    ALL_AND_ME = "all_and_me"
    JUST_ME = "just_me"
    NO_NOTIFICATIONS = "no_notifications"


class NotificationPreference:
    """Base class for notification preferences."""

    def __init__(self, user_id: str, room_id: str):
        """Initialize with user and room.

        Parameters:
            user_id (str): The user ID for which to get the push rules.
            room_id (str): The room ID for which to get the push rules.
        """
        self.user_id = user_id
        self.room_id = room_id
        self.conditions = Conditions(room_id, user_id)

    def get_push_rules(self) -> list[ConnectPushRule]:
        """
        Returns a list of ConnectPushRules. The list is a push ruleset that scopes a set of rules according to some criteria.

        For example, some rules may only be applied for messages from
        a particular sender, a particular room, or by default.
        The push ruleset contains the entire set of scopes and rules.

        Returns:
            list[ConnectPushRule]: A list of ConnectPushRules.
        """
        return []

    def get_push_rule_ids(self) -> list[str]:
        rules = self.get_push_rules()
        ids = [rule.rule_id for rule in rules]
        return sorted(ids)


class EveryMessagePreference(NotificationPreference):
    """Push Rules for the 'every_message' notification preference level."""

    def get_push_rules(self) -> list[ConnectPushRule]:
        return [
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/override/connect.room..suppress.edits;{self.room_id}",
                actions=Actions.dont_notify(),
                conditions=[
                    self.conditions.event_in_room(),
                    self.conditions.event_replaced(),
                ],
                priority_class=5,
            ),
        ]


class AllAndMePreference(NotificationPreference):
    """Push Rules for the 'all_and_me' notification preference level."""

    def get_push_rules(self):
        return [
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/underride/connect.room..mentions_or_all.disable_all;{self.room_id}",
                actions=Actions.dont_notify(),
                conditions=[self.conditions.event_in_room()],
                priority_class=1,
            ),
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/override/connect.room..mentions_or_all.mention_all;{self.room_id}",
                actions=Actions.notify(),
                conditions=[
                    self.conditions.event_in_room(),
                    self.conditions.room_mention(),
                ],
                priority_class=5,
            ),
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/override/connect.room..mentions_or_all.mention_me;{self.room_id}",
                actions=Actions.notify(),
                conditions=[
                    self.conditions.event_in_room(),
                    self.conditions.user_mention(),
                ],
                priority_class=5,
            ),
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/override/connect.room..suppress.edits;{self.room_id}",
                actions=Actions.dont_notify(),
                conditions=[
                    self.conditions.event_in_room(),
                    self.conditions.event_replaced(),
                ],
                priority_class=5,
            ),
        ]


class JustMePreference(NotificationPreference):
    """Push Rules for the 'just_me' notification preference level."""

    def get_push_rules(self):
        return [
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/underride/connect.room..only_mentions.disable_all;{self.room_id}",
                actions=Actions.dont_notify(),
                conditions=[self.conditions.event_in_room()],
                priority_class=1,
            ),
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/override/connect.room..only_mentions.disable_mention_all;{self.room_id}",
                actions=Actions.dont_notify(),
                conditions=[
                    self.conditions.event_in_room(),
                    self.conditions.room_mention(),
                ],
                priority_class=5,
            ),
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/override/connect.room..only_mentions.mention_me;{self.room_id}",
                actions=Actions.notify(),
                conditions=[
                    self.conditions.event_in_room(),
                    self.conditions.user_mention(),
                ],
                priority_class=5,
            ),
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/override/connect.room..suppress.edits;{self.room_id}",
                actions=Actions.dont_notify(),
                conditions=[
                    self.conditions.event_in_room(),
                    self.conditions.event_replaced(),
                ],
                priority_class=5,
            ),
        ]


class NoNotificationsPreference(NotificationPreference):
    """Push Rules for the 'no_notifications' notification preference level."""

    def get_push_rules(self):
        return [
            ConnectPushRule(
                user_name=self.user_id,
                rule_id=f"global/override/connect.room.disable_all;{self.room_id}",
                actions=Actions.dont_notify(),
                conditions=[self.conditions.event_in_room()],
                priority_class=5,
            )
        ]
