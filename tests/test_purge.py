"""Tests for purge module."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from purge import Module


def make_module():
    api = MagicMock()
    api.worker_name = None
    hs = MagicMock()
    hs.config.retention.retention_enabled = False
    hs.config.worker.worker_app = "not_main"
    api._hs = hs

    with patch("purge.CronTab"):
        module = Module.__new__(Module)
        module.api = api
        module.pagination_handler = MagicMock()

    return module


class PurgeTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_purge_reschedules_and_purges(self):
        module = make_module()
        module.pagination_handler.purge_history_for_rooms_in_range = AsyncMock()
        module._schedule_purge = MagicMock()

        await module._purge()

        module._schedule_purge.assert_called_once()
        module.pagination_handler.purge_history_for_rooms_in_range.assert_awaited_once_with(
            None, None
        )


if __name__ == "__main__":
    unittest.main()
