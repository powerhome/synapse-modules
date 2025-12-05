"""Model for notification preferences."""

from dataclasses import dataclass
from typing import Any, Optional, Union

from pydantic import BaseModel, Json


class Actions:
    """
    How the notification should be presented (or not presented).

    In other words: actions to take when an event matches the corresponding conditions.
    https://spec.matrix.org/v1.13/client-server-api/#actions
    """

    @staticmethod
    def dont_notify() -> list[str]:
        """
        This action prevents the event from generating a notification.

        Note: dont_notify is a historical action that's used to support current
        client-side behavior.
        https://spec.matrix.org/v1.13/client-server-api/#historical-actions

        Returns:
            list[str]: A list with the
        """
        return ["dont_notify"]

    @staticmethod
    def notify() -> list[Any]:
        """
        This is the default Connect notification action.

        Returns:
            list[str]: A list with the action.
        """
        return [
            "notify",
            {"value": "default", "set_tweak": "sound"},
            {"set_tweak": "highlight"},
        ]


@dataclass
class Conditions:
    """
    Conditions that must be met for a push rule to be applied.

    In other words: under what conditions an event should be
    passed onto a push gateway
    https://spec.matrix.org/v1.13/client-server-api/#conditions-1
    """

    room_id: str
    user_id: str

    def event_in_room(self) -> dict[str, Any]:
        """
        This condition matches any event that is in the room.

        Returns:
            dict[str, Any]: A dictionary with the condition.
        """
        return {
            "key": "room_id",
            "pattern": self.room_id,
            "kind": "event_match",
        }

    def room_mention(self) -> dict[str, Any]:
        """
        Matches any event where the room is mentioned. This is used for @all mentions.

        These mentions support encrypted events. The content.m.mentions.room metadata is a
        Boolean that's stored in plaintext, while the remaining content is encrypted,
        so Synapse can evaluate push rules without decrypting the actual message body.
        See also https://spec.matrix.org/v1.13/client-server-api/#definition-mmentions

        Returns:
            dict[str, Any]: A dictionary with the condition.
        """
        return {
            "value": True,
            "key": "content.m\\.mentions.room",
            "kind": "event_property_is",
        }

    def user_mention(self) -> dict[str, Any]:
        """
        Matches any event where the given user is mentioned in the room.

        These mentions support encrypted events. The content.m.mentions.user_ids
        metadata is a list of user_ids in plaintext. The remaining content is encrypted,
        so Synapse can evaluate push rules without decrypting the actual message body.
        See also https://spec.matrix.org/v1.13/client-server-api/#definition-mmentions

        Returns:
            dict[str, Any]: A dictionary with the condition.
        """
        return {
            "key": "content.m\\.mentions.user_ids",
            "value": self.user_id,
            "kind": "event_property_contains",
        }

    def event_replaced(self) -> dict[str, Any]:
        """
        Matches any event that is a replacement of another event.

        Returns:
            dict[str, Any]: A dictionary with the condition.
        """
        return {
            "key": "content.m\\.relates_to.rel_type",
            "value": "m.replace",
            "kind": "event_property_is",
        }


class ConnectPushRule(BaseModel):
    """Model for notification preferences."""

    id: Optional[int] = None
    user_name: str
    rule_id: str
    priority_class: int = 5
    priority: Optional[int] = None
    conditions: Union[Json[Any], list[Any]]
    actions: Union[Json[Any], list[Any]]
    enabled: bool = True
