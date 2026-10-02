"""Tests for guard."""

import unittest
from unittest.mock import AsyncMock, MagicMock, Mock

from synapse.api.constants import EventTypes

from connect.broadcast_rooms.guard import BroadcastRoomGuard
from connect.broadcast_rooms.store import BroadcastRoomStore

room_id = "!abc:localhost"
a_id = "@a:localhost"
b_id = "@b:localhost"
c_id = "@c:localhost"


def mock_event(event_type: EventTypes, room_id: str, sender: str):
    event = Mock()
    event.type = event_type
    event.room_id = room_id
    event.sender = sender
    event.redacts = None
    return event


types_to_test = {EventTypes.Message, EventTypes.Redaction, EventTypes.Encrypted}


class BroadcastRoomGuardTestSuite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.main_store = MagicMock()
        self.store = MagicMock(spec=BroadcastRoomStore)
        self.guard = BroadcastRoomGuard(self.main_store, self.store)

    async def test__can_create_edit_delete_message__allows_reactions(self):
        event = mock_event(EventTypes.Reaction, room_id, c_id)

        self.store.get_broadcasters = AsyncMock(return_value=[a_id, b_id])

        self.assertTrue(await self.guard.can_create_edit_delete_message(event))

        self.main_store.get_event.assert_not_called()
        self.store.get_broadcasters.assert_not_called()

    async def test__can_create_edit_delete_message__allows_redacting_reactions(self):
        reaction_event = mock_event(EventTypes.Reaction, room_id, c_id)
        reaction_event.event_id = "$ABC"

        redaction_event = mock_event(EventTypes.Redaction, room_id, c_id)
        redaction_event.redacts = reaction_event.event_id

        self.main_store.get_event = AsyncMock(return_value=reaction_event)
        self.store.get_broadcasters = AsyncMock(return_value=[a_id, b_id])

        self.assertTrue(
            await self.guard.can_create_edit_delete_message(redaction_event)
        )

        self.main_store.get_event.assert_called_once_with(
            reaction_event.event_id, allow_none=True
        )
        self.store.get_broadcasters.assert_not_called()

    async def test__can_create_edit_delete_message__allows_when_not_broadcast_room(
        self,
    ):
        for type in types_to_test:
            with self.subTest(type=type):
                event = mock_event(type, room_id, a_id)

                self.main_store.reset_mock()
                self.store.get_broadcasters = AsyncMock(return_value=None)

                self.assertTrue(await self.guard.can_create_edit_delete_message(event))

                self.main_store.get_event.assert_not_called()
                self.store.get_broadcasters.assert_called_once_with(room_id)

    async def test__can_create_edit_delete_message__denies_when_no_broadcasters(self):
        for type in types_to_test:
            with self.subTest(type=type):
                event = mock_event(type, room_id, a_id)

                self.main_store.reset_mock()
                self.store.get_broadcasters = AsyncMock(return_value=[])

                self.assertFalse(await self.guard.can_create_edit_delete_message(event))

                self.main_store.get_event.assert_not_called()
                self.store.get_broadcasters.assert_called_once_with(room_id)

    async def test__can_create_edit_delete_message__allows_broadcaster(self):
        for type in types_to_test:
            with self.subTest(type=type):
                event = mock_event(type, room_id, a_id)

                self.main_store.reset_mock()
                self.store.get_broadcasters = AsyncMock(return_value=[a_id, b_id])

                self.assertTrue(await self.guard.can_create_edit_delete_message(event))

                self.main_store.get_event.assert_not_called()
                self.store.get_broadcasters.assert_called_once_with(room_id)

    async def test__can_create_edit_delete_message__denies_non_broadcaster(self):
        for type in types_to_test:
            with self.subTest(type=type):
                event = mock_event(type, room_id, c_id)

                self.main_store.reset_mock()
                self.store.get_broadcasters = AsyncMock(return_value=[a_id, b_id])

                self.assertFalse(await self.guard.can_create_edit_delete_message(event))

                self.main_store.get_event.assert_not_called()
                self.store.get_broadcasters.assert_called_once_with(room_id)


if __name__ == "__main__":
    unittest.main()
