"""Tests for the broadcast rooms module configuration."""

import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from connect.broadcast_rooms import Config, Module
from connect.v2_bridge import BridgeNotifier

BASE_CONFIG = {"allowed_localparts": ["bridge", "connect_bot"]}
BRIDGE_CONFIG = {"hs_token": "bridge-token", "base_url": "http://connect-v2:29328"}


class ConfigTestSuite(unittest.TestCase):
    def test_todays_config_is_still_valid(self):
        # Every new setting is optional, so the module loads with the
        # configuration that was deployed before they existed.
        config = Config.model_validate(BASE_CONFIG)

        self.assertEqual(
            config.broadcast_updated_event_type, "com.powerhrg.broadcast.updated"
        )
        # The feature toggle is off unless a deployment turns it on.
        self.assertFalse(config.admin_writes_enabled)

    def test_requires_allowed_localparts(self):
        with self.assertRaises(ValidationError):
            Config.model_validate({"allowed_localparts": []})

    def test_accepts_a_bridge(self):
        config = Config.model_validate({**BASE_CONFIG, "bridge": BRIDGE_CONFIG})

        self.assertEqual(str(config.bridge.base_url), "http://connect-v2:29328/")

    def test_a_bridge_needs_its_token(self):
        with self.assertRaises(ValidationError):
            Config.model_validate(
                {**BASE_CONFIG, "bridge": {"base_url": "http://connect-v2:29328"}}
            )

    def test_accepts_the_admin_writes_toggle(self):
        config = Config.model_validate({**BASE_CONFIG, "admin_writes_enabled": True})

        self.assertTrue(config.admin_writes_enabled)

    def test_does_not_leak_secrets_in_validation_errors(self):
        with self.assertRaises(ValidationError) as ctx:
            Config.model_validate(
                {**BASE_CONFIG, "bridge": {"hs_token": "super-secret"}}
            )

        self.assertNotIn("super-secret", str(ctx.exception))


@patch("connect.broadcast_rooms.BroadcastRoomResource")
@patch("connect.broadcast_rooms.BroadcastRoomHandler")
@patch("connect.broadcast_rooms.BroadcastRoomGuard")
@patch("connect.broadcast_rooms.BroadcastRoomStore")
class ModuleTestSuite(unittest.TestCase):
    def test_wires_the_handler_and_the_resource(
        self, store_cls, _guard_cls, handler_cls, resource_cls
    ):
        api = MagicMock()
        config = {**BASE_CONFIG, "admin_writes_enabled": True}

        Module(config, api)

        handler_cls.assert_called_once_with(
            api._hs, api, store_cls.return_value, config, None
        )
        resource_cls.assert_called_once_with(
            api._hs,
            handler_cls.return_value,
            api,
            BASE_CONFIG["allowed_localparts"],
            True,
        )

    def test_the_toggle_is_off_when_absent(
        self, _store_cls, _guard_cls, handler_cls, resource_cls
    ):
        api = MagicMock()

        Module(dict(BASE_CONFIG), api)

        self.assertFalse(resource_cls.call_args.args[4])
        api.register_web_resource.assert_called_once()
        api.register_spam_checker_callbacks.assert_called_once()

    def test_gives_the_handler_a_bridge_notifier_when_bridged(
        self, _store_cls, _guard_cls, handler_cls, _resource_cls
    ):
        Module({**BASE_CONFIG, "bridge": BRIDGE_CONFIG}, MagicMock())

        self.assertIsInstance(handler_cls.call_args.args[4], BridgeNotifier)

    def test_refuses_an_invalid_config(
        self, _store_cls, _guard_cls, _handler_cls, _resource_cls
    ):
        with self.assertRaises(ValidationError):
            Module({"allowed_localparts": []}, MagicMock())


if __name__ == "__main__":
    unittest.main()
