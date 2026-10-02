"""Tests for user provisioning endpoint contract."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.http.site import SynapseRequest
from synapse.types import JsonDict

from connect.populator.provisioning import ProvisioningServlet


class ProvisioningContractTestSuite(unittest.IsolatedAsyncioTestCase):
    """Contract verification tests for SCIM format provisioning."""

    def setUp(self):
        """Set up test fixtures."""
        self.hs = MagicMock()
        self.hs.get_auth = MagicMock()
        self.populator = MagicMock()
        self.populator.populate_single_user = AsyncMock()
        self.servlet = ProvisioningServlet(self.hs, self.populator)

    async def _make_authenticated_request(self, payload: JsonDict) -> tuple:
        """Helper to create authenticated request.

        Args:
            payload: JSON payload to send in the request.

        Returns:
            Tuple of (status_code, response_dict) from the endpoint.
        """
        request = MagicMock(spec=SynapseRequest)

        # Mock authentication as audiences_bot
        requester = MagicMock()
        requester.user.localpart = "audiences_bot"
        auth = self.hs.get_auth()
        auth.get_user_by_req = AsyncMock(return_value=requester)

        # Mock parse_json_object_from_request
        with patch(
            "connect.populator.provisioning.parse_json_object_from_request",
            return_value=payload,
        ):
            with patch("connect.populator.provisioning.init_request_context"):
                with patch("connect.populator.provisioning.log_event"):
                    return await self.servlet.on_POST(request)

    async def test_accepts_valid_scim_format(self):
        """Endpoint accepts valid SCIM format with camelCase keys."""
        # Given: Valid SCIM payload
        scim_payload = {
            "id": "user-123",
            "externalId": "ext-456",
            "userName": "jsmith",
            "displayName": "Jane Smith",
            "active": True,
            "groups": [{"value": "group-1", "display": "Engineering"}],
        }

        # When: POST to /_populator/user
        status, response = await self._make_authenticated_request(scim_payload)

        # Then: Returns 204 No Content
        self.assertEqual(status, HTTPStatus.NO_CONTENT)

        # And: Populator was called with SCIM payload
        self.populator.populate_single_user.assert_awaited_once()
        called_payload = self.populator.populate_single_user.call_args[0][0]
        self.assertEqual(called_payload["id"], "user-123")
        self.assertEqual(called_payload["displayName"], "Jane Smith")

    async def test_rejects_missing_required_fields(self):
        """Endpoint rejects payloads missing required SCIM fields.

        SCIM contract expects: id, userName, displayName, active.
        The endpoint validates required fields are present.
        """
        from synapse.api.errors import SynapseError

        empty_payload = {}

        with self.assertRaises(SynapseError) as ctx:
            await self._make_authenticated_request(empty_payload)

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        self.assertIn("Missing required SCIM field", ctx.exception.msg)

    async def test_rejects_non_audiences_bot_user(self):
        """Endpoint rejects requests not from audiences_bot."""
        from synapse.api.errors import SynapseError

        # Given: Request from different user
        request = MagicMock(spec=SynapseRequest)
        requester = MagicMock()
        requester.user.localpart = "other_user"
        auth = self.hs.get_auth()
        auth.get_user_by_req = AsyncMock(return_value=requester)

        # When/Then: Raises SynapseError with FORBIDDEN
        with self.assertRaises(SynapseError) as ctx:
            with patch("connect.populator.provisioning.init_request_context"):
                await self.servlet.on_POST(request)

        self.assertEqual(ctx.exception.code, HTTPStatus.FORBIDDEN)

    async def test_rejects_missing_id(self):
        """Endpoint rejects payload missing required 'id' field."""
        from synapse.api.errors import SynapseError

        payload = {
            "userName": "jsmith",
            "displayName": "Jane Smith",
            "active": True,
        }

        with self.assertRaises(SynapseError) as ctx:
            await self._make_authenticated_request(payload)

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        self.assertIn("Missing required SCIM field: 'id'", ctx.exception.msg)

    async def test_rejects_missing_user_name(self):
        """Endpoint rejects payload missing required 'userName' field."""
        from synapse.api.errors import SynapseError

        payload = {
            "id": "user-123",
            "displayName": "Jane Smith",
            "active": True,
        }

        with self.assertRaises(SynapseError) as ctx:
            await self._make_authenticated_request(payload)

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        self.assertIn("Missing required SCIM field: 'userName'", ctx.exception.msg)

    async def test_rejects_missing_display_name(self):
        """Endpoint rejects payload missing required 'displayName' field."""
        from synapse.api.errors import SynapseError

        payload = {
            "id": "user-123",
            "userName": "jsmith",
            "active": True,
        }

        with self.assertRaises(SynapseError) as ctx:
            await self._make_authenticated_request(payload)

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        self.assertIn("Missing required SCIM field: 'displayName'", ctx.exception.msg)

    async def test_rejects_missing_active(self):
        """Endpoint rejects payload missing required 'active' field."""
        from synapse.api.errors import SynapseError

        payload = {
            "id": "user-123",
            "userName": "jsmith",
            "displayName": "Jane Smith",
        }

        with self.assertRaises(SynapseError) as ctx:
            await self._make_authenticated_request(payload)

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        self.assertIn("Missing required SCIM field: 'active'", ctx.exception.msg)

    async def test_rejects_wrong_active_type(self):
        """Endpoint rejects non-boolean 'active' field."""
        from synapse.api.errors import SynapseError

        payload = {
            "id": "user-123",
            "userName": "jsmith",
            "displayName": "Jane Smith",
            "active": "true",  # Should be boolean, not string
        }

        with self.assertRaises(SynapseError) as ctx:
            await self._make_authenticated_request(payload)

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        self.assertIn("active", ctx.exception.msg)

    async def test_rejects_groups_as_non_array(self):
        """Endpoint rejects non-array 'groups' field."""
        from synapse.api.errors import SynapseError

        payload = {
            "id": "user-123",
            "userName": "jsmith",
            "displayName": "Jane Smith",
            "active": True,
            "groups": "not-an-array",
        }

        with self.assertRaises(SynapseError) as ctx:
            await self._make_authenticated_request(payload)

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        self.assertIn("groups", ctx.exception.msg)
