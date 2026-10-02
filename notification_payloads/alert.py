"""Alert content builder."""

from typing import Optional

from synapse.types import JsonDict

from .alert_strategies import AlertStrategyFactory
from .types import NotificationType


class Alert:
    """Formats notification titles based on conversation type and message type."""

    def __init__(
        self,
        content: JsonDict,
    ):
        self.content = content
        room_name = content.get("room_name") or content.get("room_alias")
        self.event_type, self.conversation_type = NotificationType.determine(
            content, room_name
        )
        self.strategy = AlertStrategyFactory.create_strategy(
            self.event_type,
            self.conversation_type,
            content,
            room_name,
            content.get("sender_display_name"),
            content.get("sender_raw_name"),
        )

    def title(self) -> Optional[str]:
        """Format notification title based on sender, room, and message type.

        Returns:
            Formatted title or None if the notification should not have a title
        """
        return self.strategy.title()

    def body(self) -> str:
        """Format notification body based on message type and content.

        Returns:
            Formatted notification body text
        """
        return self.strategy.body()

    def apns_dict(
        self,
        default_payload: Optional[JsonDict] = None,
    ) -> Optional[JsonDict]:
        """Process notification content into payload.

        Args:
            default_payload: Optional base payload to merge with notification data

        Returns:
            Processed payload
        """
        apns_alert = {"body": self.body()}
        title = self.title()
        if title:
            apns_alert["title"] = title

        apns_payload = {"alert": apns_alert}

        room_id = self.content.get("room_id")
        category_identifier = "LIKE_REPLY_CATEGORY"
        default_sound = "default"

        default_payload = default_payload or {}

        result = dict(default_payload)
        if "aps" in result:
            result["aps"].update(apns_payload)
        else:
            result["aps"] = apns_payload

        result["aps"].setdefault("category", category_identifier)
        result["aps"].setdefault("sound", default_sound)
        if room_id:
            result["aps"].setdefault("thread-id", room_id)

        return result
