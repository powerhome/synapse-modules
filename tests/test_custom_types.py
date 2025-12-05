"""Tests for custom Pydantic types."""

import unittest

from pydantic import BaseModel, ValidationError

from custom_types import CUSTOM_EVENT_TYPE


class EventModel(BaseModel):
    event_type: CUSTOM_EVENT_TYPE


class CustomEventTypeTestSuite(unittest.TestCase):
    """Tests for CUSTOM_EVENT_TYPE pattern validation."""

    def test_valid_builtin_matrix_types(self):
        """Test that built-in Matrix event types are valid."""
        valid_types = [
            "m.room.message",
            "m.room.member",
            "m.room.create",
            "m.room.name",
            "m.room.topic",
            "m.call.invite",
            "m.call.answer",
            "m.call.hangup",
        ]

        for event_type in valid_types:
            with self.subTest(event_type=event_type):
                model = EventModel(event_type=event_type)
                self.assertEqual(model.event_type, event_type)

    def test_valid_custom_types(self):
        """Test that custom event types with reverse domain notation are valid."""
        valid_types = [
            "com.example.foo",
            "com.example.foo.bar",
            "com.chatapp.room.created",
            "org.foo.custom_event",
            "io.github.myapp.notification",
            "dev.mycompany.test_event",
            "net.example.event123",
        ]

        for event_type in valid_types:
            with self.subTest(event_type=event_type):
                model = EventModel(event_type=event_type)
                self.assertEqual(model.event_type, event_type)

    def test_invalid_single_segment(self):
        """Test that single segment types are invalid."""
        invalid_types = [
            "example",
            "message",
            "m",
        ]

        for event_type in invalid_types:
            with self.subTest(event_type=event_type):
                with self.assertRaises(ValidationError):
                    EventModel(event_type=event_type)

    def test_invalid_uppercase(self):
        """Test that uppercase characters are invalid."""
        invalid_types = [
            "Com.example.foo",
            "com.Example.foo",
            "com.example.Foo",
            "M.room.message",
        ]

        for event_type in invalid_types:
            with self.subTest(event_type=event_type):
                with self.assertRaises(ValidationError):
                    EventModel(event_type=event_type)

    def test_invalid_special_characters(self):
        """Test that special characters are invalid."""
        invalid_types = [
            "example@foo",
            "example-foo.bar",
            "example.foo-bar",
            "example.foo!bar",
            "example.foo bar",
            "example.foo/bar",
        ]

        for event_type in invalid_types:
            with self.subTest(event_type=event_type):
                with self.assertRaises(ValidationError):
                    EventModel(event_type=event_type)

    def test_invalid_starting_characters(self):
        """Test that types starting with invalid characters are rejected."""
        invalid_types = [
            ".example.foo",
            "1example.foo",
            "_example.foo",
        ]

        for event_type in invalid_types:
            with self.subTest(event_type=event_type):
                with self.assertRaises(ValidationError):
                    EventModel(event_type=event_type)

    def test_valid_underscores_and_numbers(self):
        """Test that underscores and numbers are valid in segments."""
        valid_types = [
            "org.foo.custom_event",
            "com.example.event123",
            "io.app.test_event_2",
            "dev.myapp.event_test_123",
        ]

        for event_type in valid_types:
            with self.subTest(event_type=event_type):
                model = EventModel(event_type=event_type)
                self.assertEqual(model.event_type, event_type)

    def test_edge_cases(self):
        """Test edge cases for validation."""
        test_cases = [
            ("a.b", True),
            ("a.b.c.d.e.f.g", True),
            ("a1.b2.c3", True),
            ("a_.b_.c_", True),
            ("", False),
            (".", False),
            ("..", False),
            ("a.", False),
            (".a", False),
        ]

        for event_type, should_be_valid in test_cases:
            with self.subTest(event_type=event_type, should_be_valid=should_be_valid):
                if should_be_valid:
                    model = EventModel(event_type=event_type)
                    self.assertEqual(model.event_type, event_type)
                else:
                    with self.assertRaises(ValidationError):
                        EventModel(event_type=event_type)


if __name__ == "__main__":
    unittest.main()
