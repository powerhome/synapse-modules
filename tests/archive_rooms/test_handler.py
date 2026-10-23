"""Tests for archive rooms handler bridge notifications."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.api.errors import SynapseError

from connect.archive_rooms.handler import ArchiveRoomHandler

ROOM_ID = "!room:server"
IMPERSONATOR_MXID = "@u38257:server"
BRIDGE_BOT_MXID = "@connect_bot:server"
BRIDGE_CONFIG = {
    "hs_token": "secret-token",
    "base_url": "http://connect-v2:29328",
    "impersonator_mxid": IMPERSONATOR_MXID,
    "bot_mxid": BRIDGE_BOT_MXID,
}


def make_requester(mxid: str):
    requester = MagicMock()
    requester.user.__str__ = lambda _self: mxid
    return requester


class NotifyBridgeTestSuite(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, bridge_config=BRIDGE_CONFIG, bot_ids=None):
        handler = ArchiveRoomHandler.__new__(ArchiveRoomHandler)
        handler.api = MagicMock()
        handler.bot_ids = bot_ids if bot_ids is not None else ["@connect_bot:server"]
        handler.bridge_config = bridge_config
        return handler

    @patch("connect.archive_rooms.handler.BridgeClient")
    async def test_notifies_bridge_on_archive(self, bridge_client_cls):
        bridge_client = bridge_client_cls.return_value
        bridge_client.update_archive_status = AsyncMock()
        handler = self.make_handler()

        await handler._notify_bridge(ROOM_ID, True, make_requester("@alice:server"))

        # The real requester (@alice) is replaced by the configured impersonator.
        bridge_client.update_archive_status.assert_awaited_once_with(
            ROOM_ID, IMPERSONATOR_MXID, True
        )

    @patch("connect.archive_rooms.handler.BridgeClient")
    async def test_notifies_bridge_on_unarchive(self, bridge_client_cls):
        bridge_client = bridge_client_cls.return_value
        bridge_client.update_archive_status = AsyncMock()
        handler = self.make_handler()

        await handler._notify_bridge(ROOM_ID, False, make_requester("@alice:server"))

        bridge_client.update_archive_status.assert_awaited_once_with(
            ROOM_ID, IMPERSONATOR_MXID, False
        )

    @patch("connect.archive_rooms.handler.BridgeClient")
    async def test_skips_when_requester_is_bridge_bot(self, bridge_client_cls):
        handler = self.make_handler()

        await handler._notify_bridge(ROOM_ID, True, make_requester(BRIDGE_BOT_MXID))

        bridge_client_cls.assert_not_called()

    @patch("connect.archive_rooms.handler.BridgeClient")
    async def test_notifies_when_requester_is_audiences_bot(self, bridge_client_cls):
        # Auto-archival runs as the audiences bot. That is a genuine v3-originated
        # change and must reach v2, so it must NOT be treated like the bridge bot.
        bridge_client = bridge_client_cls.return_value
        bridge_client.update_archive_status = AsyncMock()
        handler = self.make_handler()

        await handler._notify_bridge(
            ROOM_ID, True, make_requester("@audiences_bot:server")
        )

        bridge_client.update_archive_status.assert_awaited_once_with(
            ROOM_ID, IMPERSONATOR_MXID, True
        )

    @patch("connect.archive_rooms.handler.BridgeClient")
    async def test_skips_when_bridge_not_configured(self, bridge_client_cls):
        handler = self.make_handler(bridge_config=None)

        await handler._notify_bridge(ROOM_ID, True, make_requester("@alice:server"))

        bridge_client_cls.assert_not_called()

    @patch("connect.archive_rooms.handler.BridgeClient")
    async def test_bridge_failure_does_not_raise(self, bridge_client_cls):
        bridge_client = bridge_client_cls.return_value
        bridge_client.update_archive_status = AsyncMock(
            side_effect=Exception("bridge unreachable")
        )
        handler = self.make_handler()

        await handler._notify_bridge(ROOM_ID, True, make_requester("@alice:server"))

        bridge_client.update_archive_status.assert_awaited_once()


class HandlePutSelfDmGuardTestSuite(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, is_self_dm=False):
        handler = ArchiveRoomHandler.__new__(ArchiveRoomHandler)
        handler.api = MagicMock()
        # No public join rule -> visibility branch is skipped.
        handler.api.get_room_state = AsyncMock(return_value={})
        handler.main_store = MagicMock()
        handler.main_store.block_room = AsyncMock()
        handler.main_store.unblock_room = AsyncMock()
        handler.main_store.set_room_is_public = AsyncMock()
        handler.archive_store = MagicMock()
        handler.archive_store.is_self_dm = AsyncMock(return_value=is_self_dm)
        handler.update_memberships_directly = AsyncMock()
        handler.audiences_services_enabled = False
        handler.bridge_config = None
        return handler

    async def test_archiving_self_dm_raises_and_does_not_mutate(self):
        handler = self.make_handler(is_self_dm=True)

        with self.assertRaises(SynapseError) as ctx:
            await handler.handle_put(
                ROOM_ID, archive=True, requester=make_requester("@alice:server")
            )

        self.assertEqual(ctx.exception.code, 400)
        handler.archive_store.is_self_dm.assert_awaited_once_with(ROOM_ID)
        handler.main_store.block_room.assert_not_called()
        handler.update_memberships_directly.assert_not_called()

    async def test_archiving_non_self_dm_proceeds(self):
        handler = self.make_handler(is_self_dm=False)

        status, body = await handler.handle_put(
            ROOM_ID, archive=True, requester=make_requester("@alice:server")
        )

        self.assertEqual(body, {"room_id": ROOM_ID, "archive": True})
        handler.archive_store.is_self_dm.assert_awaited_once_with(ROOM_ID)
        handler.main_store.block_room.assert_awaited_once()
        handler.update_memberships_directly.assert_awaited_once()

    async def test_unarchiving_skips_self_dm_check(self):
        handler = self.make_handler(is_self_dm=True)

        status, body = await handler.handle_put(
            ROOM_ID, archive=False, requester=make_requester("@alice:server")
        )

        self.assertEqual(body, {"room_id": ROOM_ID, "archive": False})
        # The guard only applies to archival; unarchive must never be blocked.
        handler.archive_store.is_self_dm.assert_not_called()
        handler.main_store.unblock_room.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
