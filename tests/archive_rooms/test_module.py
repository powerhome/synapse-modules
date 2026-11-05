"""Tests for archive rooms module auto-archive job."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connect.archive_rooms import Module

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


if __name__ == "__main__":
    unittest.main()
