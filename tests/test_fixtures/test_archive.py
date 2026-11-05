"""Tests for the room archiver."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fixtures.archive import RoomArchiver


class ArchiveTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_archives_through_the_real_archive_handler_as_the_archiver(self):
        handler = MagicMock()
        handler.handle_put = AsyncMock(return_value=(200, {}))
        with patch("fixtures.archive.ArchiveRoomHandler", return_value=handler):
            archiver = RoomArchiver(MagicMock(), bot_user_ids=[])

            await archiver.archive("!room:localhost", "@alice:localhost")

        handler.handle_put.assert_awaited_once()
        args = handler.handle_put.await_args
        self.assertEqual(args.args, ("!room:localhost",))
        self.assertIs(args.kwargs["archive"], True)
        self.assertEqual(args.kwargs["requester"].user.to_string(), "@alice:localhost")

    async def test_hands_the_bot_ids_to_the_handler_so_bots_are_not_parted(self):
        with patch("fixtures.archive.ArchiveRoomHandler") as handler_class:
            RoomArchiver(MagicMock(), bot_user_ids=["@audiences_bot:localhost"])

        config = handler_class.call_args.args[2]
        self.assertEqual(config["bot_user_ids"], ["@audiences_bot:localhost"])

    async def test_runs_the_handler_without_a_bridge_or_audiences_restore(self):
        with patch("fixtures.archive.ArchiveRoomHandler") as handler_class:
            RoomArchiver(MagicMock(), bot_user_ids=[])

        # No bridge: seeding never notifies Connect v2 of the archival.
        config = handler_class.call_args.args[2]
        self.assertNotIn("bridge", config)
        self.assertFalse(config["audiences_services_enabled"])


if __name__ == "__main__":
    unittest.main()
