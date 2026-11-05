"""Tests for alert strategy factory."""

import unittest

from notification_payloads.alert_strategies import (
    AlertStrategyFactory,
    CallAlertStrategy,
    MessageWithContentStrategy,
    MessageWithoutContentStrategy,
)
from notification_payloads.types import ConversationTypeEnum, EventTypeEnum


class AlertStrategyFactoryTestSuite(unittest.TestCase):
    def test_creates_call_strategy_for_dm(self):
        content = {"type": "m.call.invite"}
        strategy = AlertStrategyFactory.create_strategy(
            EventTypeEnum.CALL, ConversationTypeEnum.DM, content, None, "Bob", "Bob"
        )

        self.assertIsInstance(strategy, CallAlertStrategy)
        self.assertEqual(strategy.sender_display_name, "Bob")

    def test_creates_call_strategy_for_room(self):
        content = {"type": "m.call.invite"}
        strategy = AlertStrategyFactory.create_strategy(
            EventTypeEnum.CALL,
            ConversationTypeEnum.ROOM,
            content,
            "Conference Room",
            "Bob",
            "Bob",
        )

        self.assertIsInstance(strategy, CallAlertStrategy)
        self.assertEqual(strategy.room_name, "Conference Room")

    def test_creates_message_without_content_strategy_for_dm(self):
        content = {"type": "m.room.message"}
        strategy = AlertStrategyFactory.create_strategy(
            EventTypeEnum.MESSAGE_WITHOUT_CONTENT,
            ConversationTypeEnum.DM,
            content,
            None,
            "Charlie",
            "Charlie",
        )

        self.assertIsInstance(strategy, MessageWithoutContentStrategy)
        self.assertEqual(strategy.sender_raw_name, "Charlie")

    def test_creates_message_without_content_strategy_for_room(self):
        content = {"type": "m.room.encrypted"}
        strategy = AlertStrategyFactory.create_strategy(
            EventTypeEnum.MESSAGE_WITHOUT_CONTENT,
            ConversationTypeEnum.ROOM,
            content,
            "Secret Room",
            "Charlie",
            "Charlie",
        )

        self.assertIsInstance(strategy, MessageWithoutContentStrategy)
        self.assertEqual(strategy.room_name, "Secret Room")

    def test_creates_message_with_content_strategy_for_dm(self):
        content = {"type": "m.room.message", "content": {"body": "Hello world"}}
        strategy = AlertStrategyFactory.create_strategy(
            EventTypeEnum.MESSAGE_WITH_CONTENT,
            ConversationTypeEnum.DM,
            content,
            None,
            "Dave",
            "Dave",
        )

        self.assertIsInstance(strategy, MessageWithContentStrategy)
        self.assertEqual(strategy.conversation_type, ConversationTypeEnum.DM)
        self.assertEqual(strategy.sender_display_name, "Dave")

    def test_creates_message_with_content_strategy_for_room(self):
        content = {"type": "m.room.message", "content": {"body": "Team meeting at 3pm"}}
        strategy = AlertStrategyFactory.create_strategy(
            EventTypeEnum.MESSAGE_WITH_CONTENT,
            ConversationTypeEnum.ROOM,
            content,
            "Project Updates",
            "Dave",
            "Dave",
        )

        self.assertIsInstance(strategy, MessageWithContentStrategy)
        self.assertEqual(strategy.conversation_type, ConversationTypeEnum.ROOM)
        self.assertEqual(strategy.room_name, "Project Updates")

    def test_defaults_to_message_with_content_strategy_for_unknown_event_type(self):
        content = {"type": "m.room.unknown"}
        strategy = AlertStrategyFactory.create_strategy(
            "UNKNOWN_EVENT", ConversationTypeEnum.DM, content, None, "Eve", "Eve"
        )

        self.assertIsInstance(strategy, MessageWithContentStrategy)

    def test_factory_preserves_all_parameters(self):
        content = {
            "type": "m.room.message",
            "content": {"body": "Test message", "msgtype": "m.text"},
            "extra": "data",
        }
        room_name = "Test Room with Special Characters 🚀"
        sender_display_name = "[staging] Test User"
        sender_raw_name = "Test User"
        conversation_type = ConversationTypeEnum.ROOM

        strategy = AlertStrategyFactory.create_strategy(
            EventTypeEnum.MESSAGE_WITH_CONTENT,
            conversation_type,
            content,
            room_name,
            sender_display_name,
            sender_raw_name,
        )

        self.assertEqual(strategy.content, content)
        self.assertEqual(strategy.room_name, room_name)
        self.assertEqual(strategy.sender_display_name, sender_display_name)
        self.assertEqual(strategy.sender_raw_name, sender_raw_name)
        self.assertEqual(strategy.conversation_type, conversation_type)

    def test_factory_handles_none_values(self):
        strategy = AlertStrategyFactory.create_strategy(
            EventTypeEnum.MESSAGE_WITH_CONTENT,
            ConversationTypeEnum.DM,
            {},
            None,
            None,
            None,
        )

        self.assertIsInstance(strategy, MessageWithContentStrategy)
        self.assertIsNone(strategy.room_name)
        self.assertIsNone(strategy.sender_display_name)
        self.assertIsNone(strategy.sender_raw_name)

    def test_factory_raises_assertion_for_invite_events(self):
        with self.assertRaises(AssertionError):
            AlertStrategyFactory.create_strategy(
                EventTypeEnum.ROOM_MEMBERSHIP,
                ConversationTypeEnum.DM,
                {},
                None,
                "Alice",
                "Alice",
            )

    def test_factory_handles_empty_strings(self):
        strategy = AlertStrategyFactory.create_strategy(
            EventTypeEnum.CALL, ConversationTypeEnum.ROOM, {}, "", "", ""
        )

        self.assertIsInstance(strategy, CallAlertStrategy)
        self.assertEqual(strategy.room_name, "")
        self.assertEqual(strategy.sender_display_name, "")
        self.assertEqual(strategy.sender_raw_name, "")


if __name__ == "__main__":
    unittest.main()
