"""Tests for bridge authenticator."""

import unittest
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

from synapse.module_api import NOT_SPAM, ModuleApi

import connect.bridge as bridge
from connect.bridge import Authenticator

a_id = "@a:localhost"
bot_id = "@bot:localhost"
config = {
    "enabled": True,
    "idp_id": "idp_id",
    "bot_user_id": bot_id,
    "hs_token": "1234",
    "bridge_base_url": "http://test:1234",
    "remote_user_id_field": "remote_user_id",
}

if TYPE_CHECKING:
    from synapse.server import HomeServer  # noqa: F401


class AuthenticatorTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_check_login_for_spam__not_enabled(self):
        disabled_config = {
            "enabled": False,
            "idp_id": "idp_id",
            "bot_user_id": bot_id,
            "hs_token": "1234",
            "bridge_base_url": "http://test:1234",
            "remote_user_id_field": "remote_user_id",
        }
        mock_api = create_autospec(ModuleApi)
        mock_api._hs = create_autospec("HomeServer")

        authenticator = Authenticator(disabled_config, mock_api)
        result = await authenticator.check_login_for_spam(a_id, None, None, None)
        self.assertEqual(result, NOT_SPAM)

    async def test_check_login_for_spam_from_different_oidc_provider(self):
        mock_api = create_autospec(ModuleApi)
        mock_api._hs = create_autospec("HomeServer")

        authenticator = Authenticator(config, mock_api)
        result = await authenticator.check_login_for_spam(
            a_id, None, None, None, auth_provider_id="oidc-other"
        )
        self.assertEqual(result, NOT_SPAM)

    async def test_check_login_for_spam__bot_user_id(self):
        mock_api = create_autospec(ModuleApi)
        mock_api._hs = create_autospec("HomeServer")

        authenticator = Authenticator(config, mock_api)
        result = await authenticator.check_login_for_spam(
            bot_id, None, None, None, auth_provider_id="oidc-idp_id"
        )
        self.assertEqual(result, NOT_SPAM)

    async def test_check_login_for_spam__no_session(self):
        bridge.oidc_sessions = {}

        mock_api = create_autospec(ModuleApi)
        mock_api._hs = create_autospec("HomeServer")
        authenticator = Authenticator(config, mock_api)
        result = await authenticator.check_login_for_spam(
            a_id, None, None, None, auth_provider_id="oidc-idp_id"
        )
        self.assertEqual(result, NOT_SPAM)


if __name__ == "__main__":
    unittest.main()
