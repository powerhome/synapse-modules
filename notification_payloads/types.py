"""Notification type classification."""

from enum import Enum
from typing import Optional, Tuple

from synapse.types import JsonDict


class EventTypeEnum(Enum):
    """Enumeration of event types for notifications."""

    ROOM_MEMBERSHIP = "ROOM_MEMBERSHIP"
    CALL = "CALL"
    MESSAGE_WITH_CONTENT = "MESSAGE_WITH_CONTENT"
    MESSAGE_WITHOUT_CONTENT = "MESSAGE_WITHOUT_CONTENT"


class ConversationTypeEnum(Enum):
    """Enumeration of conversation types."""

    DM = "DM"
    ROOM = "ROOM"


class NotificationType:
    """Represents the type of notification."""

    @staticmethod
    def determine(
        content: JsonDict, room_name: Optional[str]
    ) -> Tuple[EventTypeEnum, ConversationTypeEnum]:
        """Determines the notification type from the notification content.

        Args:
            content: The notification content dictionary
            room_name: The name of the room

        Returns:
            Tuple of EventTypeEnum and ConversationTypeEnum based on the content
        """
        event_type = NotificationType.get_event_type(content)
        conversation_type = NotificationType.get_conversation_type(room_name)
        return event_type, conversation_type

    @staticmethod
    def get_event_type(content: JsonDict) -> EventTypeEnum:
        """Determines the event type from the notification content.

        Args:
            content: The notification content dictionary

        Returns:
            EventTypeEnum based on the content
        """
        event_type = content.get("type", "")
        if event_type == "m.call.invite":
            return EventTypeEnum.CALL

        if event_type == "m.room.member":
            return EventTypeEnum.ROOM_MEMBERSHIP

        if "content" in content and "body" in content["content"]:
            return EventTypeEnum.MESSAGE_WITH_CONTENT

        return EventTypeEnum.MESSAGE_WITHOUT_CONTENT

    @staticmethod
    def get_conversation_type(room_name: Optional[str]) -> ConversationTypeEnum:
        """Determines the conversation type from the notification content.

        Args:
            room_name: The name of the room

        Returns:
            ConversationTypeEnum based on the content
        """
        if room_name:
            return ConversationTypeEnum.ROOM

        return ConversationTypeEnum.DM
