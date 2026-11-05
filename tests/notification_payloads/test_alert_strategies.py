"""Tests for alert strategies."""

import unittest

from notification_payloads.alert_strategies import (
    FALLBACK_BODY,
    CallAlertStrategy,
    MessageWithContentStrategy,
    MessageWithoutContentStrategy,
)
from notification_payloads.types import ConversationTypeEnum


class CallAlertStrategyTestSuite(unittest.TestCase):
    def test_video_call_with_sender(self):
        content = {
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "type": "m.call.invite",
            "content": {"offer": {"sdp": "v=0\nm=video 5004 RTP/AVP 96"}},
        }
        strategy = CallAlertStrategy(content, None, "Alice", "Alice")

        self.assertIsNone(strategy.title())
        self.assertEqual(strategy.body(), "Alice wants to Video Call")

    def test_voice_call_with_sender(self):
        content = {
            "sender_display_name": "Bob",
            "sender_raw_name": "Bob",
            "type": "m.call.invite",
            "content": {"offer": {"sdp": "v=0\nm=audio 5004 RTP/AVP 0"}},
        }
        strategy = CallAlertStrategy(content, None, "Bob", "Bob")

        self.assertIsNone(strategy.title())
        self.assertEqual(strategy.body(), "Bob wants to Voice Call")

    def test_call_no_sdp_defaults_to_voice(self):
        content = {
            "sender_display_name": "Charlie",
            "sender_raw_name": "Charlie",
            "type": "m.call.invite",
            "content": {"offer": {}},
        }
        strategy = CallAlertStrategy(content, None, "Charlie", "Charlie")

        self.assertIsNone(strategy.title())
        self.assertEqual(strategy.body(), "Charlie wants to Voice Call")

    def test_call_empty_content_defaults_to_voice(self):
        content = {
            "sender_display_name": "Dave",
            "sender_raw_name": "Dave",
            "type": "m.call.invite",
        }
        strategy = CallAlertStrategy(content, None, "Dave", "Dave")

        self.assertIsNone(strategy.title())
        self.assertEqual(strategy.body(), "Dave wants to Voice Call")

    def test_call_no_sender_returns_fallback(self):
        content = {
            "type": "m.call.invite",
            "content": {"offer": {"sdp": "v=0\nm=video 5004 RTP/AVP 96"}},
        }
        strategy = CallAlertStrategy(content, None, None, None)

        self.assertIsNone(strategy.title())
        self.assertEqual(strategy.body(), FALLBACK_BODY)

    def test_call_malformed_content_structure(self):
        content = {
            "sender_display_name": "Eve",
            "sender_raw_name": "Eve",
            "type": "m.call.invite",
            "content": "not a dict",
        }
        strategy = CallAlertStrategy(content, None, "Eve", "Eve")

        self.assertIsNone(strategy.title())
        with self.assertRaises(AttributeError):
            strategy.body()


class MessageWithoutContentStrategyTestSuite(unittest.TestCase):
    def test_message_without_content_dm_with_sender(self):
        content = {"sender_display_name": "Alice", "sender_raw_name": "Alice"}
        strategy = MessageWithoutContentStrategy(content, None, "Alice", "Alice")

        self.assertIsNone(strategy.title())
        self.assertEqual(strategy.body(), "Alice is Connecting with you")

    def test_message_without_content_room_with_sender_and_room(self):
        content = {
            "sender_display_name": "Bob",
            "sender_raw_name": "Bob",
            "room_name": "General",
        }
        strategy = MessageWithoutContentStrategy(content, "General", "Bob", "Bob")

        self.assertIsNone(strategy.title())
        self.assertEqual(strategy.body(), "Bob wrote a message in General")

    def test_message_without_content_room_with_sender_no_room(self):
        content = {"sender_display_name": "Charlie", "sender_raw_name": "Charlie"}
        strategy = MessageWithoutContentStrategy(content, None, "Charlie", "Charlie")

        self.assertIsNone(strategy.title())
        self.assertEqual(strategy.body(), "Charlie is Connecting with you")

    def test_message_without_content_no_sender_returns_fallback(self):
        content = {"room_name": "Test Room"}
        strategy = MessageWithoutContentStrategy(content, "Test Room", None, None)

        self.assertIsNone(strategy.title())
        self.assertEqual(strategy.body(), FALLBACK_BODY)


class MessageWithContentStrategyTestSuite(unittest.TestCase):
    def test_message_with_content_dm(self):
        content = {
            "sender_display_name": "[dev] Alice",
            "sender_raw_name": "Alice",
            "content": {"body": "Hello world!"},
        }
        strategy = MessageWithContentStrategy(
            content, None, "[dev] Alice", "Alice", ConversationTypeEnum.DM
        )

        self.assertEqual(strategy.title(), "[dev] Alice")
        self.assertEqual(strategy.body(), "Hello world!")

    def test_message_with_content_room_with_room_name(self):
        content = {
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "room_name": "Project Updates",
            "content": {"body": "Meeting at 3pm"},
        }
        strategy = MessageWithContentStrategy(
            content, "Project Updates", "Alice", "Alice", ConversationTypeEnum.ROOM
        )

        self.assertEqual(strategy.title(), "Alice in Project Updates")
        self.assertEqual(strategy.body(), "Meeting at 3pm")

    def test_message_with_content_room_without_room_name(self):
        content = {
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "content": {"body": "Hello"},
        }
        strategy = MessageWithContentStrategy(
            content, None, "Alice", "Alice", ConversationTypeEnum.ROOM
        )

        self.assertEqual(strategy.title(), "Alice")
        self.assertEqual(strategy.body(), "Hello")

    def test_message_with_content_empty_body_returns_fallback(self):
        content = {
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "content": {"body": ""},
        }
        strategy = MessageWithContentStrategy(
            content, None, "Alice", "Alice", ConversationTypeEnum.DM
        )

        self.assertEqual(strategy.title(), "Alice")
        self.assertEqual(strategy.body(), FALLBACK_BODY)

    def test_message_with_content_none_body_returns_fallback(self):
        content = {
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "content": {"body": None},
        }
        strategy = MessageWithContentStrategy(
            content, None, "Alice", "Alice", ConversationTypeEnum.DM
        )

        self.assertEqual(strategy.title(), "Alice")
        self.assertEqual(strategy.body(), FALLBACK_BODY)

    def test_message_with_content_missing_body_returns_fallback(self):
        content = {
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "content": {},
        }
        strategy = MessageWithContentStrategy(
            content, None, "Alice", "Alice", ConversationTypeEnum.DM
        )

        self.assertEqual(strategy.title(), "Alice")
        self.assertEqual(strategy.body(), FALLBACK_BODY)

    def test_message_with_content_missing_content_returns_fallback(self):
        content = {"sender_display_name": "Alice", "sender_raw_name": "Alice"}
        strategy = MessageWithContentStrategy(
            content, None, "Alice", "Alice", ConversationTypeEnum.DM
        )

        self.assertEqual(strategy.title(), "Alice")
        self.assertEqual(strategy.body(), FALLBACK_BODY)

    def test_title_with_none_sender_display_name(self):
        content = {"room_name": "Test Room", "content": {"body": "Hello"}}
        strategy = MessageWithContentStrategy(
            content, "Test Room", None, "Alice", ConversationTypeEnum.ROOM
        )

        self.assertEqual(strategy.title(), "None in Test Room")

    def test_message_body_truncated_at_1024_characters(self):
        long_message = "a" * 2000
        content = {
            "sender_display_name": "Alice",
            "sender_raw_name": "Alice",
            "content": {"body": long_message},
        }
        strategy = MessageWithContentStrategy(
            content, None, "Alice", "Alice", ConversationTypeEnum.DM
        )

        result_body = strategy.body()
        self.assertEqual(len(result_body), 1024)
        self.assertEqual(result_body, "a" * 1024)

    def test_message_body_under_1024_characters_not_truncated(self):
        short_message = "Hello, this is a regular message."
        content = {
            "sender_display_name": "Bob",
            "sender_raw_name": "Bob",
            "content": {"body": short_message},
        }
        strategy = MessageWithContentStrategy(
            content, None, "Bob", "Bob", ConversationTypeEnum.DM
        )

        self.assertEqual(strategy.body(), short_message)

    def test_message_body_exactly_1024_characters_not_truncated(self):
        exact_message = "b" * 1024
        content = {
            "sender_display_name": "Charlie",
            "sender_raw_name": "Charlie",
            "content": {"body": exact_message},
        }
        strategy = MessageWithContentStrategy(
            content, None, "Charlie", "Charlie", ConversationTypeEnum.ROOM
        )

        result_body = strategy.body()
        self.assertEqual(len(result_body), 1024)
        self.assertEqual(result_body, exact_message)


if __name__ == "__main__":
    unittest.main()
