"""Tests for notification types module."""

import unittest

from notification_payloads.types import (
    ConversationTypeEnum,
    EventTypeEnum,
    NotificationType,
)


class NotificationTypeTestSuite(unittest.TestCase):
    """Comprehensive tests for NotificationType methods."""

    def test_get_conversation_type_with_room_name(self):
        """Test get_conversation_type returns ROOM when room_name is provided."""
        test_cases = [
            "Project Updates",
            "General",
            "ABC123",
            "Secret Room",
            "#general:example.com",
            "A",
            "Room with spaces and special characters!@#$%",
            "😀 Emoji Room 🎉",
            " Room with leading/trailing spaces ",
        ]

        for room_name in test_cases:
            with self.subTest(room_name=room_name):
                result = NotificationType.get_conversation_type(room_name)
                self.assertEqual(result, ConversationTypeEnum.ROOM)

    def test_get_conversation_type_without_room_name(self):
        """Test get_conversation_type returns DM when room_name is None or empty."""
        test_cases = [
            None,
            "",
        ]

        for room_name in test_cases:
            with self.subTest(room_name=room_name):
                result = NotificationType.get_conversation_type(room_name)
                self.assertEqual(result, ConversationTypeEnum.DM)

    def test_get_event_type_call_invite(self):
        """Test get_event_type returns CALL for m.call.invite events."""
        test_cases = [
            {"type": "m.call.invite"},
            {"type": "m.call.invite", "content": {"offer": {"sdp": "some_sdp"}}},
            {"type": "m.call.invite", "sender_display_name": "Alice"},
        ]

        for content in test_cases:
            with self.subTest(content=content):
                result = NotificationType.get_event_type(content)
                self.assertEqual(result, EventTypeEnum.CALL)

    def test_get_event_type_member_events(self):
        """Test get_event_type returns ROOM_MEMBERSHIP for all m.room.member events."""
        test_cases = [
            {"type": "m.room.member", "membership": "invite"},
            {"type": "m.room.member", "membership": "invite", "user_is_target": True},
            {"type": "m.room.member", "membership": "invite", "room_name": "Test Room"},
        ]

        for content in test_cases:
            with self.subTest(content=content):
                result = NotificationType.get_event_type(content)
                self.assertEqual(result, EventTypeEnum.ROOM_MEMBERSHIP)

    def test_get_event_type_member_all_types(self):
        """Test get_event_type returns ROOM_MEMBERSHIP for all m.room.member events regardless of membership type."""
        test_cases = [
            {"type": "m.room.member", "membership": "join"},
            {"type": "m.room.member", "membership": "leave"},
            {"type": "m.room.member", "membership": "ban"},
            {"type": "m.room.member", "membership": "knock"},
            {"type": "m.room.member"},
            {"type": "m.room.member", "membership": "INVITE"},
        ]

        for content in test_cases:
            with self.subTest(content=content):
                result = NotificationType.get_event_type(content)
                self.assertEqual(result, EventTypeEnum.ROOM_MEMBERSHIP)

    def test_get_event_type_message_with_content(self):
        """Test get_event_type returns MESSAGE_WITH_CONTENT for messages with body."""
        test_cases = [
            {"content": {"body": "Hello"}},
            {"type": "m.room.message", "content": {"body": "Hello"}},
            {"content": {"body": ""}},
            {"content": {"body": " "}},
            {"content": {"body": "A very long message " * 100}},
            {"content": {"body": "https://example.com"}},
            {"content": {"body": "GIF by Someone https://giphy.com/..."}},
            {"content": {"body": "test", "msgtype": "m.text"}},
            {"content": {"body": "image.jpg", "msgtype": "m.image"}},
            {"content": {"body": "waves hello", "msgtype": "m.emote"}},
            {"type": "m.room.encrypted", "content": {"body": "fallback text"}},
        ]

        for content in test_cases:
            with self.subTest(content=content):
                result = NotificationType.get_event_type(content)
                self.assertEqual(result, EventTypeEnum.MESSAGE_WITH_CONTENT)

    def test_get_event_type_message_without_content(self):
        """Test get_event_type returns MESSAGE_WITHOUT_CONTENT for messages without body."""
        test_cases = [
            {},
            {"type": "m.room.message"},
            {"content": {}},
            {"content": {"msgtype": "m.text"}},
            {"content": {"other_field": "value"}},
            {"body": "Hello"},
            {"type": "m.room.message", "body": "Hello"},
            {"type": "m.room.encrypted", "content": {}},
            {"type": "m.room.unknown"},
            {"type": "custom.event"},
            {"sender": "Alice"},
        ]

        for content in test_cases:
            with self.subTest(content=content):
                result = NotificationType.get_event_type(content)
                self.assertEqual(result, EventTypeEnum.MESSAGE_WITHOUT_CONTENT)

    def test_get_event_type_priority(self):
        """Test get_event_type priority when multiple conditions match."""
        content = {"type": "m.call.invite", "content": {"body": "This is a call"}}
        result = NotificationType.get_event_type(content)
        self.assertEqual(result, EventTypeEnum.CALL)

        content = {
            "type": "m.room.member",
            "membership": "invite",
            "content": {"body": "Join my room"},
        }
        result = NotificationType.get_event_type(content)
        self.assertEqual(result, EventTypeEnum.ROOM_MEMBERSHIP)

    def test_determine_combinations(self):
        """Test determine method with various combinations."""
        test_cases = [
            (
                {"content": {"body": "Hello"}},
                None,
                (EventTypeEnum.MESSAGE_WITH_CONTENT, ConversationTypeEnum.DM),
            ),
            (
                {"content": {"body": "Hello"}},
                "General",
                (EventTypeEnum.MESSAGE_WITH_CONTENT, ConversationTypeEnum.ROOM),
            ),
            (
                {"type": "m.room.member", "membership": "invite"},
                None,
                (EventTypeEnum.ROOM_MEMBERSHIP, ConversationTypeEnum.DM),
            ),
            (
                {"type": "m.room.member", "membership": "invite"},
                "Project Room",
                (EventTypeEnum.ROOM_MEMBERSHIP, ConversationTypeEnum.ROOM),
            ),
            (
                {"type": "m.call.invite"},
                None,
                (EventTypeEnum.CALL, ConversationTypeEnum.DM),
            ),
            (
                {"type": "m.call.invite"},
                "Conference Room",
                (EventTypeEnum.CALL, ConversationTypeEnum.ROOM),
            ),
            (
                {},
                None,
                (EventTypeEnum.MESSAGE_WITHOUT_CONTENT, ConversationTypeEnum.DM),
            ),
            (
                {},
                "Mystery Room",
                (EventTypeEnum.MESSAGE_WITHOUT_CONTENT, ConversationTypeEnum.ROOM),
            ),
        ]

        for content, room_name, expected in test_cases:
            with self.subTest(content=content, room_name=room_name):
                result = NotificationType.determine(content, room_name)
                self.assertEqual(result, expected)

    def test_edge_cases(self):
        """Test various edge cases."""
        special_rooms = [
            "🚀",
            "Room\nWith\nNewlines",
            "Room\tWith\tTabs",
            "\u200b",
            "💬🔥✨",
        ]

        for room in special_rooms:
            result = NotificationType.get_conversation_type(room)
            self.assertEqual(result, ConversationTypeEnum.ROOM)


if __name__ == "__main__":
    unittest.main()
