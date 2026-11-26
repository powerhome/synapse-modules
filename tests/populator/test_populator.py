"""Tests for Populator SCIM field extraction."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connect.populator.populator import Populator


class PopulatorScimExtractionTestSuite(unittest.TestCase):
    """Tests for SCIM format field extraction."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = {"hs_token": "test_token", "idp_id": "test_idp"}
        self.hs = MagicMock()
        self.populator = Populator(self.config, self.hs)

    def test_extracts_id_field_for_external_user_id(self):
        """Populator extracts 'id' field for external user ID."""
        # Given: SCIM format user with "id"
        user = {"id": "user-123", "displayName": "Jane Smith", "active": True}

        # When: Extract external user ID
        result = self.populator._external_user_id_for_user(user)

        # Then: Returns id value
        self.assertEqual(result, "user-123")


class PopulatorDisplayNameTestSuite(unittest.IsolatedAsyncioTestCase):
    """Tests for displayName extraction."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = {"hs_token": "test_token", "idp_id": "test_idp"}
        self.hs = MagicMock()
        self.hs.hostname = "test.example.com"
        self.populator = Populator(self.config, self.hs)

    async def test_extracts_displayName_for_profile_updates(self):
        """Populator extracts 'displayName' (camelCase) for profile updates."""
        # Given: SCIM format with displayName
        users = [
            {
                "id": "user-123",
                "displayName": "Jane Smith",
                "active": True,
                "matrix_localpart": "jsmith",
            }
        ]

        # Mock display name lookup
        self.populator.db_pool = MagicMock()
        self.populator.db_pool.simple_select_list = AsyncMock(
            return_value=[("jsmith", "Old Name")]
        )
        self.populator.api = MagicMock()
        self.populator.api.set_displayname = AsyncMock()

        # When: Update display names
        await self.populator.update_display_names(users)

        # Then: displayName field was extracted and used
        self.populator.api.set_displayname.assert_awaited()
        call_args = self.populator.api.set_displayname.call_args[0]
        self.assertEqual(call_args[1], "Jane Smith")


class PopulatorActiveStatusTestSuite(unittest.IsolatedAsyncioTestCase):
    """Tests for active status extraction."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = {"hs_token": "test_token", "idp_id": "test_idp"}
        self.hs = MagicMock()
        self.hs.hostname = "test.example.com"
        self.populator = Populator(self.config, self.hs)

    async def test_extracts_active_status_for_activation(self):
        """Populator extracts 'active' field for user activation."""
        # Given: SCIM format with active=True
        user = {"id": "user-123", "active": True, "matrix_localpart": "jsmith"}

        # Mock dependencies
        from connect.helpers.user import UserHelpers

        with patch.object(
            UserHelpers, "is_user_deactivated", AsyncMock(return_value=True)
        ):
            self.hs.get_deactivate_account_handler = MagicMock()
            handler = MagicMock()
            handler.activate_account = AsyncMock()
            self.hs.get_deactivate_account_handler.return_value = handler

            # When: Toggle user active
            await self.populator.toggle_user_active(user)

            # Then: User was activated based on active=True
            handler.activate_account.assert_awaited()

    async def test_extracts_active_status_for_deactivation(self):
        """Populator extracts 'active' field for user deactivation."""
        # Given: SCIM format with active=False
        user = {"id": "user-456", "active": False, "matrix_localpart": "bdoe"}

        # Mock dependencies
        from connect.helpers.user import UserHelpers

        with patch.object(
            UserHelpers, "is_user_deactivated", AsyncMock(return_value=False)
        ):
            self.populator.store = MagicMock()
            self.populator._deactivate_user_by_mxid = AsyncMock()

            # When: Toggle user active
            await self.populator.toggle_user_active(user)

            # Then: User was deactivated based on active=False
            self.populator._deactivate_user_by_mxid.assert_awaited()
