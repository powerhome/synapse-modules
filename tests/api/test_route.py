"""Tests for logic in resources file."""

import unittest
from dataclasses import dataclass
from random import randint
from unittest.mock import Mock, call, patch

from api.batch_room_tag.routes import RoomTagResource, logger


class FakeAccountDataHandler:
    def __init__(self):
        pass

    async def add_tag_to_room(self, a, b, c, d):
        pass


class FakeHomeServer:
    def __init__(self):
        pass

    def get_profile_handler(self):
        pass

    def get_auth(self):
        pass

    def get_account_data_handler(self):
        return FakeAccountDataHandler()


@dataclass
class Tag:
    room_id: str
    tag_name: str
    content: None


class RoomTagEndpointTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test__call_room_tag(self):
        tags = [
            Tag("1", "testing", None),
            Tag("2", "testing", None),
            Tag("3", "testing", None),
        ]
        hs = FakeHomeServer()
        resource = RoomTagResource(hs)
        mock_debugger = Mock()
        mock_request = Mock()
        with patch.object(logger, "debug", mock_debugger):
            await resource._call_room_tag(mock_request, tags, "test")
            calls = [
                call("test added testing room tag to room 1"),
                call("test added testing room tag to room 2"),
                call("test added testing room tag to room 3"),
            ]
            mock_debugger.assert_has_calls(calls)

    async def test__call_room_tag_random_data(self):
        total = randint(50, 100)  # noqa: S311
        tags = [Tag(f"{i}", "testing", None) for i in range(total)]
        hs = FakeHomeServer()
        resource = RoomTagResource(hs)
        mock_debugger = Mock()
        mock_request = Mock()
        with patch.object(logger, "debug", mock_debugger):
            await resource._call_room_tag(mock_request, tags, "test")
            calls = [
                call(f"test added testing room tag to room {i}") for i in range(total)
            ]
            mock_debugger.assert_has_calls(calls)
