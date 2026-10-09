"""Tests for handler."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import SynapseError

from connect.broadcast_rooms.handler import BroadcastRoomHandler
from connect.broadcast_rooms.store import BroadcastRoomStore

room_id = "!abc:localhost"
a_id = "@a:localhost"
b_id = "@b:localhost"


class BroadcastRoomHandlerTestSuite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = MagicMock(spec=BroadcastRoomStore)
        self.handler = BroadcastRoomHandler(self.store)

    async def test__handle_get__not_broadcast_room(self):
        self.store.get_broadcasters = AsyncMock(return_value=None)

        code, _ = await self.handler.handle_get(room_id)
        self.assertEqual(code, 404)
        self.store.get_broadcasters.assert_called_once_with(room_id)

    async def test__handle_get__no_broadcasters(self):
        self.store.get_broadcasters = AsyncMock(return_value=[])

        code, body = await self.handler.handle_get(room_id)
        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [])
        self.store.get_broadcasters.assert_called_once_with(room_id)

    async def test__handle_get__ok(self):
        self.store.get_broadcasters = AsyncMock(return_value=[a_id, b_id])

        code, body = await self.handler.handle_get(room_id)
        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [a_id, b_id])
        self.store.get_broadcasters.assert_called_once_with(room_id)

    async def test__handle_put__missing_key(self):
        with self.assertRaises(SynapseError) as ctx:
            await self.handler.handle_put(room_id, {})

        self.assertEqual(ctx.exception.code, 400)

    async def test__handle_put__not_list(self):
        with self.assertRaises(SynapseError) as ctx:
            await self.handler.handle_put(room_id, {"broadcasters": 123})

        self.assertEqual(ctx.exception.code, 400)

    async def test__handle_put__not_only_strings(self):
        with self.assertRaises(SynapseError) as ctx:
            await self.handler.handle_put(room_id, {"broadcasters": [123]})

        self.assertEqual(ctx.exception.code, 400)

    async def test__handle_put__has_duplicates(self):
        code, body = await self.handler.handle_put(
            room_id, {"broadcasters": [a_id, a_id]}
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [a_id])
        self.store.set_broadcasters.assert_called_once_with(room_id, [a_id])

    async def test__handle_put__ok(self):
        code, body = await self.handler.handle_put(
            room_id, {"broadcasters": [a_id, b_id]}
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["broadcasters"], [a_id, b_id])
        self.store.set_broadcasters.assert_called_once_with(room_id, [a_id, b_id])

    async def test__handle_delete__ok(self):
        code, body = await self.handler.handle_delete(room_id)
        self.assertEqual(code, 200)
        self.assertEqual(body, {})
        self.store.unbroadcast_room.assert_called_once_with(room_id)


if __name__ == "__main__":
    unittest.main()
