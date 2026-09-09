"""Alert formatting strategies using composition pattern."""

from abc import ABC, abstractmethod
from typing import Optional

from synapse.types import JsonDict

from .types import ConversationTypeEnum, EventTypeEnum

FALLBACK_BODY = "Someone is connecting with you..."


class AlertStrategy(ABC):
    """Base strategy for alert formatting."""

    def __init__(
        self,
        content: JsonDict,
        room_name: Optional[str],
        sender_display_name: Optional[str],
        sender_raw_name: Optional[str],
    ):
        self.content = content
        self.room_name = room_name
        self.sender_display_name = sender_display_name
        self.sender_raw_name = sender_raw_name

    @abstractmethod
    def title(self) -> Optional[str]:
        pass

    @abstractmethod
    def body(self) -> str:
        pass


class CallAlertStrategy(AlertStrategy):
    """Strategy for call notifications."""

    def title(self) -> Optional[str]:
        return None

    def body(self) -> str:
        if not self.sender_raw_name:
            return FALLBACK_BODY

        sdp = self.content.get("content", {}).get("offer", {}).get("sdp", "")
        if "m=video" in sdp:
            return f"{self.sender_raw_name} wants to Video Call"
        else:
            return f"{self.sender_raw_name} wants to Voice Call"


class MessageWithoutContentStrategy(AlertStrategy):
    """Strategy for messages without content."""

    def title(self) -> Optional[str]:
        return None

    def body(self) -> str:
        if not self.sender_raw_name:
            return FALLBACK_BODY

        if self.room_name:
            return f"{self.sender_raw_name} wrote a message in {self.room_name}"
        return f"{self.sender_raw_name} is Connecting with you"


class MessageWithContentStrategy(AlertStrategy):
    """Strategy for messages with content."""

    def __init__(
        self,
        content: JsonDict,
        room_name: Optional[str],
        sender_display_name: Optional[str],
        sender_raw_name: Optional[str],
        conversation_type: ConversationTypeEnum,
    ):
        super().__init__(content, room_name, sender_display_name, sender_raw_name)
        self.conversation_type = conversation_type

    def title(self) -> Optional[str]:
        if self.conversation_type == ConversationTypeEnum.ROOM and self.room_name:
            return f"{self.sender_display_name} in {self.room_name}"
        return self.sender_display_name

    def body(self) -> str:
        message_body = self.content.get("content", {}).get("body")
        if not message_body:
            return FALLBACK_BODY

        # Truncate to 1024 characters to fit within APNs limits
        # https://developer.apple.com/library/archive/documentation/NetworkingInternet/Conceptual/RemoteNotificationsPG/CreatingtheNotificationPayload.html
        truncated_body = message_body[:1024]

        return truncated_body


class AlertStrategyFactory:
    """Factory for creating alert strategies."""

    @staticmethod
    def create_strategy(
        event_type: EventTypeEnum,
        conversation_type: ConversationTypeEnum,
        content: JsonDict,
        room_name: Optional[str],
        sender_display_name: Optional[str],
        sender_raw_name: Optional[str],
    ) -> AlertStrategy:
        assert event_type != EventTypeEnum.ROOM_MEMBERSHIP

        if event_type == EventTypeEnum.CALL:
            return CallAlertStrategy(
                content, room_name, sender_display_name, sender_raw_name
            )
        elif event_type == EventTypeEnum.MESSAGE_WITHOUT_CONTENT:
            return MessageWithoutContentStrategy(
                content, room_name, sender_display_name, sender_raw_name
            )
        else:
            return MessageWithContentStrategy(
                content,
                room_name,
                sender_display_name,
                sender_raw_name,
                conversation_type,
            )
