"""Tests for archive rooms module auto-archive job."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError

from connect.archive_rooms import Config, Module
from connect.v2_bridge import BridgeNotifier

BASE_CONFIG = {
    "bot_user_ids": ["@audiences_bot:server"],
    "hs_token": "secret-token",
    "idp_id": "nitroid",
    "audiences_services_enabled": True,
    "audiences_bot_user_id": "@audiences_bot:server",
}


def make_api(is_main_process=True):
    api = MagicMock()
    api.worker_name = None if is_main_process else "synapse-generic-worker"
    return api


class RunAutoArchiveTestSuite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = Module.__new__(Module)
        self.module.store = MagicMock()
        self.module._handler = MagicMock()
        self.module._bot_user_id = "@audiences_bot:server"
        self.module._schedule_auto_archive = MagicMock()

    async def test_archives_all_eligible_rooms(self):
        self.module.store.get_rooms_eligible_for_auto_archive = AsyncMock(
            return_value=["!room1:server", "!room2:server"]
        )
        self.module._handler.handle_put = AsyncMock()

        await self.module._run_auto_archive()

        self.assertEqual(self.module._handler.handle_put.call_count, 2)
        for call_args in self.module._handler.handle_put.call_args_list:
            self.assertEqual(call_args.kwargs["archive"], True)

    async def test_no_rooms_archived_when_none_eligible(self):
        self.module.store.get_rooms_eligible_for_auto_archive = AsyncMock(
            return_value=[]
        )
        self.module._handler.handle_put = AsyncMock()

        await self.module._run_auto_archive()

        self.module._handler.handle_put.assert_not_called()

    async def test_continues_after_per_room_failure(self):
        self.module.store.get_rooms_eligible_for_auto_archive = AsyncMock(
            return_value=["!room1:server", "!room2:server", "!room3:server"]
        )
        self.module._handler.handle_put = AsyncMock(
            side_effect=[Exception("something went wrong"), None, None]
        )

        await self.module._run_auto_archive()

        self.assertEqual(self.module._handler.handle_put.call_count, 3)

    async def test_returns_early_when_db_fetch_fails(self):
        self.module.store.get_rooms_eligible_for_auto_archive = AsyncMock(
            side_effect=Exception("db connection failed")
        )
        self.module._handler.handle_put = AsyncMock()

        await self.module._run_auto_archive()

        self.module._handler.handle_put.assert_not_called()


@patch("connect.archive_rooms.CronTab")
@patch("connect.archive_rooms.ArchiveRoomResource")
@patch("connect.archive_rooms.ArchiveRoomHandler")
@patch("connect.archive_rooms.ArchiveRoomStore")
class AutoArchivalSchedulingTestSuite(unittest.TestCase):
    def test_scheduled_when_flag_enabled(self, _store, _handler, _resource, _crontab):
        api = make_api()
        clock = api._hs.get_clock.return_value
        Module({**BASE_CONFIG, "auto_archival_enabled": True}, api)
        clock.call_later.assert_called_once()

    def test_not_scheduled_when_flag_disabled(
        self, _store, _handler, _resource, _crontab
    ):
        api = make_api()
        clock = api._hs.get_clock.return_value
        Module({**BASE_CONFIG, "auto_archival_enabled": False}, api)
        clock.call_later.assert_not_called()

    def test_not_scheduled_on_worker_process(
        self, _store, _handler, _resource, _crontab
    ):
        api = make_api(is_main_process=False)
        clock = api._hs.get_clock.return_value
        Module({**BASE_CONFIG, "auto_archival_enabled": True}, api)
        clock.call_later.assert_not_called()


BRIDGE_CONFIG = {
    "hs_token": "bridge-token",
    "base_url": "http://connect-v2:29328",
    "bot_mxid": "@connect_bot:server",
}


class BridgeConfigTestSuite(unittest.TestCase):
    def test_needs_no_impersonator(self):
        Config.model_validate({**BASE_CONFIG, "bridge": BRIDGE_CONFIG})

    def test_requires_the_bridge_bot(self):
        bridge = {key: BRIDGE_CONFIG[key] for key in ("hs_token", "base_url")}

        with self.assertRaises(ValidationError):
            Config.model_validate({**BASE_CONFIG, "bridge": bridge})


@patch("connect.archive_rooms.ArchiveRoomResource")
@patch("connect.archive_rooms.ArchiveRoomHandler")
@patch("connect.archive_rooms.ArchiveRoomStore")
class BridgeWiringTestSuite(unittest.TestCase):
    def test_gives_the_handler_a_bridge_notifier(self, _store, handler_cls, _resource):
        api = make_api()

        Module({**BASE_CONFIG, "bridge": BRIDGE_CONFIG}, api)

        bridge = handler_cls.call_args.args[3]
        self.assertIsInstance(bridge, BridgeNotifier)

    def test_has_no_bridge_notifier_without_a_bridge(
        self, _store, handler_cls, _resource
    ):
        Module(dict(BASE_CONFIG), make_api())

        self.assertIsNone(handler_cls.call_args.args[3])


if __name__ == "__main__":
    unittest.main()
