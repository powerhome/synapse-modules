"""Tests for sending a fixtures message so a repeat is harmless."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from fixtures.sending import send_once


class SendOnceTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_sends_from_the_fixtures_device_with_the_txn_id(self):
        handler = MagicMock()
        event = MagicMock(event_id="$e")
        handler.create_and_send_nonmember_event = AsyncMock(return_value=(event, 1))

        result = await send_once(handler, "@a:hs", {"type": "m.room.message"}, "t1")

        call = handler.create_and_send_nonmember_event.await_args
        requester = call.args[0]
        self.assertEqual(requester.user.to_string(), "@a:hs")
        self.assertEqual(requester.device_id, "FIXTURES")
        self.assertEqual(call.kwargs["txn_id"], "t1")
        self.assertFalse(call.kwargs["ratelimit"])
        self.assertIs(result, event)


if __name__ == "__main__":
    unittest.main()
