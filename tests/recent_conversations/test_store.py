"""Tests for recent conversations store."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from recent_conversations.store import (
    LatestEvent,
    LatestEventsResult,
    RecentConversationsStore,
)


class RecentConversationsStoreTestSuite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db_pool = MagicMock()
        self.store = RecentConversationsStore(self.db_pool)

    async def test_fetch_latest_events_for_rooms_empty_list(self):
        result = await self.store.fetch_latest_events_for_rooms([])

        self.assertIsInstance(result, LatestEventsResult)
        self.assertEqual(result.to_dict(), {})
        self.db_pool.runInteraction.assert_not_called()

    async def test_fetch_latest_events_for_rooms_no_events(self):
        room_ids = ["!room1:server", "!room2:server"]

        mock_txn = MagicMock()
        mock_txn.fetchone.return_value = [{}]

        self.db_pool.runInteraction = AsyncMock(return_value={})

        result = await self.store.fetch_latest_events_for_rooms(room_ids)

        self.assertIsInstance(result, LatestEventsResult)
        self.assertEqual(result.to_dict(), {})
        self.db_pool.runInteraction.assert_called_once()

    async def test_fetch_latest_events_for_rooms_single_room(self):
        room_ids = ["!room1:server"]
        mock_result = {
            "!room1:server": LatestEvent(
                event_id="$event1",
                type="m.room.message",
                sender="@user:server",
                origin_server_ts=1234567890,
            )
        }

        self.db_pool.runInteraction = AsyncMock(return_value=mock_result)

        result = await self.store.fetch_latest_events_for_rooms(room_ids)

        self.assertIsInstance(result, LatestEventsResult)
        result_dict = result.to_dict()

        self.assertEqual(len(result_dict), 1)
        self.assertIn("!room1:server", result_dict)
        self.assertEqual(result_dict["!room1:server"]["event_id"], "$event1")
        self.assertEqual(result_dict["!room1:server"]["type"], "m.room.message")
        self.assertEqual(result_dict["!room1:server"]["sender"], "@user:server")
        self.assertEqual(result_dict["!room1:server"]["origin_server_ts"], 1234567890)

        self.db_pool.runInteraction.assert_called_once_with(
            "fetch_latest_events_for_rooms", unittest.mock.ANY
        )

    async def test_fetch_latest_events_for_rooms_multiple_rooms(self):
        room_ids = ["!room1:server", "!room2:server", "!room3:server"]
        mock_result = {
            "!room1:server": LatestEvent(
                event_id="$event1",
                type="m.room.message",
                sender="@user1:server",
                origin_server_ts=1234567890,
            ),
            "!room2:server": LatestEvent(
                event_id="$event2",
                type="m.room.create",
                sender="@user2:server",
                origin_server_ts=1234567891,
            ),
        }

        self.db_pool.runInteraction = AsyncMock(return_value=mock_result)

        result = await self.store.fetch_latest_events_for_rooms(room_ids)

        self.assertIsInstance(result, LatestEventsResult)
        result_dict = result.to_dict()

        self.assertEqual(len(result_dict), 2)
        self.assertIn("!room1:server", result_dict)
        self.assertIn("!room2:server", result_dict)
        self.assertNotIn("!room3:server", result_dict)

        self.assertEqual(result_dict["!room1:server"]["event_id"], "$event1")
        self.assertEqual(result_dict["!room2:server"]["event_id"], "$event2")

    async def test_fetch_latest_events_for_rooms_mixed_results(self):
        room_ids = ["!room1:server", "!room2:server"]
        mock_result = {
            "!room1:server": LatestEvent(
                event_id="$event1",
                type="m.room.message",
                sender="@user:server",
                origin_server_ts=1234567890,
            ),
        }

        self.db_pool.runInteraction = AsyncMock(return_value=mock_result)

        result = await self.store.fetch_latest_events_for_rooms(room_ids)

        self.assertIsInstance(result, LatestEventsResult)

        items = list(result._events.items())
        self.assertEqual(len(items), 1)

        room1_event = next(
            event for room_id, event in items if room_id == "!room1:server"
        )

        self.assertIsInstance(room1_event, LatestEvent)

        result_dict = result.to_dict()
        self.assertEqual(len(result_dict), 1)
        self.assertIn("!room1:server", result_dict)
        self.assertNotIn("!room2:server", result_dict)


class LatestEventTestSuite(unittest.TestCase):
    def test_latest_event_to_dict(self):
        event = LatestEvent(
            event_id="$event123",
            type="m.room.message",
            sender="@alice:server",
            origin_server_ts=1234567890,
        )

        result = event.to_dict()

        expected = {
            "event_id": "$event123",
            "type": "m.room.message",
            "sender": "@alice:server",
            "origin_server_ts": 1234567890,
        }

        self.assertEqual(result, expected)

    def test_latest_event_attributes(self):
        event = LatestEvent(
            event_id="$event123",
            type="m.room.message",
            sender="@alice:server",
            origin_server_ts=1234567890,
        )

        self.assertEqual(event.event_id, "$event123")
        self.assertEqual(event.type, "m.room.message")
        self.assertEqual(event.sender, "@alice:server")
        self.assertEqual(event.origin_server_ts, 1234567890)


class LatestEventsResultTestSuite(unittest.TestCase):
    def test_latest_events_result_to_dict_with_events(self):
        events = {
            "!room1:server": LatestEvent(
                event_id="$event1",
                type="m.room.message",
                sender="@user1:server",
                origin_server_ts=1234567890,
            ),
            "!room2:server": LatestEvent(
                event_id="$event2",
                type="m.room.create",
                sender="@user2:server",
                origin_server_ts=1234567891,
            ),
        }

        result = LatestEventsResult(events)
        result_dict = result.to_dict()

        expected = {
            "!room1:server": {
                "event_id": "$event1",
                "type": "m.room.message",
                "sender": "@user1:server",
                "origin_server_ts": 1234567890,
            },
            "!room2:server": {
                "event_id": "$event2",
                "type": "m.room.create",
                "sender": "@user2:server",
                "origin_server_ts": 1234567891,
            },
        }

        self.assertEqual(result_dict, expected)

    def test_latest_events_result_to_dict_only_valid_events(self):
        events = {
            "!room1:server": LatestEvent(
                event_id="$event1",
                type="m.room.message",
                sender="@user:server",
                origin_server_ts=1234567890,
            ),
            "!room3:server": LatestEvent(
                event_id="$event3",
                type="m.room.create",
                sender="@user3:server",
                origin_server_ts=1234567892,
            ),
        }

        result = LatestEventsResult(events)
        result_dict = result.to_dict()

        self.assertEqual(len(result_dict), 2)
        self.assertIn("!room1:server", result_dict)
        self.assertIn("!room3:server", result_dict)

    def test_latest_events_result_items(self):
        events = {
            "!room1:server": LatestEvent(
                event_id="$event1",
                type="m.room.message",
                sender="@user:server",
                origin_server_ts=1234567890,
            ),
        }

        result = LatestEventsResult(events)
        items = list(result._events.items())

        self.assertEqual(len(items), 1)

        room_ids = [room_id for room_id, _ in items]
        self.assertIn("!room1:server", room_ids)

        room1_event = next(
            event for room_id, event in items if room_id == "!room1:server"
        )

        self.assertIsInstance(room1_event, LatestEvent)

    def test_latest_events_result_empty(self):
        result = LatestEventsResult({})

        self.assertEqual(result.to_dict(), {})
        self.assertEqual(list(result._events.items()), [])


if __name__ == "__main__":
    unittest.main()
