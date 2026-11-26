"""Tests for the Connect v2 bridge client."""

import json
import unittest
from unittest.mock import AsyncMock

from twisted.python.failure import Failure
from twisted.web.client import ResponseDone

from connect.v2_bridge import BridgeClient, BridgeRefusedError, BridgeUnavailableError

ROOM_ID = "!room:server"
ALICE = "@alice:server"


class FakeResponse:
    """An IResponse whose body is delivered at once."""

    def __init__(self, code: int, body: bytes = b"OK"):
        self.code = code
        self.phrase = b""
        self.body = body

    def deliverBody(self, protocol):
        protocol.dataReceived(self.body)
        protocol.connectionLost(Failure(ResponseDone()))


def make_http_client(response: FakeResponse):
    http_client = AsyncMock()
    http_client.request = AsyncMock(return_value=response)
    return http_client


def sent_request(http_client):
    return http_client.request.await_args.kwargs


class BridgeClientTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_sends_an_archive_change_with_the_hs_token(self):
        http_client = make_http_client(FakeResponse(200))
        client = BridgeClient(http_client, "hs-token", "http://connect-v2:29328")

        await client.update_archive_status(ROOM_ID, True, ALICE)

        request = sent_request(http_client)
        self.assertEqual(request["method"], "PUT")
        self.assertEqual(
            request["uri"],
            "http://connect-v2:29328/_connect/v2/rooms/%21room%3Aserver/archive",
        )
        self.assertEqual(
            request["headers"].getRawHeaders(b"Authorization"), [b"Bearer hs-token"]
        )
        self.assertEqual(
            json.loads(request["data"]), {"archive": True, "matrix_user_id": ALICE}
        )

    async def test_sends_a_broadcasters_change(self):
        http_client = make_http_client(FakeResponse(200))
        client = BridgeClient(http_client, "hs-token", "http://connect-v2:29328")

        await client.update_broadcasters(ROOM_ID, [ALICE], ALICE)

        request = sent_request(http_client)
        self.assertEqual(
            request["uri"],
            "http://connect-v2:29328/_connect/v2/rooms/%21room%3Aserver/broadcasters",
        )
        self.assertEqual(
            json.loads(request["data"]),
            {"broadcasters": [ALICE], "matrix_user_id": ALICE},
        )

    async def test_a_4xx_is_a_refusal(self):
        http_client = make_http_client(
            FakeResponse(422, b"Broadcasters without a v2 user")
        )
        client = BridgeClient(http_client, "hs-token", "http://connect-v2:29328")

        with self.assertRaises(BridgeRefusedError) as ctx:
            await client.update_broadcasters(ROOM_ID, [ALICE], ALICE)

        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("Broadcasters without a v2 user", str(ctx.exception))

    async def test_a_5xx_means_unavailable(self):
        http_client = make_http_client(FakeResponse(502, b"nitro-web is unavailable"))
        client = BridgeClient(http_client, "hs-token", "http://connect-v2:29328")

        with self.assertRaises(BridgeUnavailableError):
            await client.update_archive_status(ROOM_ID, False, ALICE)

    async def test_lets_connection_errors_through(self):
        http_client = AsyncMock()
        http_client.request = AsyncMock(side_effect=ConnectionRefusedError())
        client = BridgeClient(http_client, "hs-token", "http://connect-v2:29328")

        with self.assertRaises(ConnectionRefusedError):
            await client.update_archive_status(ROOM_ID, True, ALICE)


if __name__ == "__main__":
    unittest.main()
