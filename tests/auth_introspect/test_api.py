"""Unit tests for the auth introspect resource."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import (
    AuthError,
    Codes,
    InvalidClientTokenError,
    MissingClientTokenError,
    UnrecognizedRequestError,
)
from synapse.types import UserID

from connect.auth_introspect.api import LOCALPART_HEADER, IntrospectResource

TOKEN = b"Bearer syt_dTUyNTYw_secret"


class FakeRequest:
    """Records what the resource writes, like a minimal `SynapseRequest`."""

    def __init__(self, method: bytes = b"GET"):
        self.method = method
        self.requestHeaders = {b"Authorization": TOKEN}
        self.code = None
        self.headers: dict[bytes, bytes] = {}
        self.body = b""
        self.finish_calls = 0

    def setResponseCode(self, code):
        self.code = code

    def setHeader(self, name, value):
        self.headers[name] = value

    def write(self, data):
        self.body += data

    def finish(self):
        self.finish_calls += 1


def make_resource(get_user_by_req: AsyncMock) -> IntrospectResource:
    hs = MagicMock()
    hs.get_auth.return_value.get_user_by_req = get_user_by_req
    return IntrospectResource(hs)


def valid_user(mxid: str = "@u52560:connect.powerhrg.com") -> AsyncMock:
    return AsyncMock(return_value=MagicMock(user=UserID.from_string(mxid)))


class IntrospectValidTokenTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_valid_user_gets_204_with_localpart_header(self):
        request = FakeRequest()

        result = await make_resource(valid_user())._async_render_GET(request)

        self.assertIsNone(result)
        self.assertEqual(request.code, HTTPStatus.NO_CONTENT)
        self.assertEqual(request.headers[LOCALPART_HEADER], b"u52560")

    async def test_header_carries_only_the_localpart_not_the_mxid(self):
        request = FakeRequest()

        await make_resource(valid_user("@u1:server"))._async_render_GET(request)

        self.assertEqual(request.headers[LOCALPART_HEADER], b"u1")

    async def test_valid_user_response_has_no_body_and_finishes_once(self):
        request = FakeRequest()

        await make_resource(valid_user())._async_render_GET(request)

        self.assertEqual(request.body, b"")
        self.assertEqual(request.finish_calls, 1)

    async def test_authenticates_with_guests_and_expired_tokens_disallowed(self):
        get_user_by_req = valid_user()
        request = FakeRequest()

        await make_resource(get_user_by_req)._async_render_GET(request)

        get_user_by_req.assert_awaited_once_with(request)

    async def test_response_never_echoes_the_token(self):
        request = FakeRequest()

        await make_resource(valid_user())._async_render_GET(request)

        self.assertEqual(set(request.headers), {LOCALPART_HEADER})
        self.assertNotIn(b"syt_", b"".join(request.headers.values()))


class IntrospectRejectionTestSuite(unittest.IsolatedAsyncioTestCase):
    REJECTIONS = {
        "missing token": MissingClientTokenError(),
        "invalid token": InvalidClientTokenError(),
        "expired token": InvalidClientTokenError(
            "Access token has expired", soft_logout=True
        ),
        "guest": AuthError(
            403, "Guest access not allowed", errcode=Codes.GUEST_ACCESS_FORBIDDEN
        ),
        "locked user": AuthError(
            401, "User account has been locked", errcode=Codes.USER_LOCKED
        ),
        "expired account": AuthError(
            403, "User account has expired", errcode=Codes.EXPIRED_ACCOUNT
        ),
    }

    async def test_every_rejection_is_a_bodiless_401_without_localpart(self):
        for name, error in self.REJECTIONS.items():
            with self.subTest(name):
                request = FakeRequest()
                resource = make_resource(AsyncMock(side_effect=error))

                result = await resource._async_render_GET(request)

                self.assertIsNone(result)
                self.assertEqual(request.code, HTTPStatus.UNAUTHORIZED)
                self.assertNotIn(LOCALPART_HEADER, request.headers)
                self.assertEqual(request.body, b"")
                self.assertEqual(request.finish_calls, 1)

    async def test_unexpected_error_propagates_instead_of_answering(self):
        request = FakeRequest()
        resource = make_resource(AsyncMock(side_effect=RuntimeError("db down")))

        with self.assertRaises(RuntimeError):
            await resource._async_render_GET(request)

        self.assertIsNone(request.code)
        self.assertEqual(request.finish_calls, 0)


class IntrospectMethodTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_head_is_served_like_get(self):
        request = FakeRequest(method=b"HEAD")

        await make_resource(valid_user())._async_render(request)

        self.assertEqual(request.code, HTTPStatus.NO_CONTENT)

    async def test_other_methods_are_rejected_before_auth(self):
        get_user_by_req = valid_user()
        request = FakeRequest(method=b"POST")

        with self.assertRaises(UnrecognizedRequestError) as ctx:
            await make_resource(get_user_by_req)._async_render(request)

        self.assertEqual(ctx.exception.code, HTTPStatus.METHOD_NOT_ALLOWED)
        get_user_by_req.assert_not_awaited()
