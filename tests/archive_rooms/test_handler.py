"""Tests for archive rooms handler bridge notifications."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.api.constants import Membership
from synapse.api.errors import SynapseError
from synapse.types import create_requester

from connect.archive_rooms.handler import ArchiveRoomHandler

ROOM_ID = "!room:server"
BRIDGE_BOT_MXID = "@connect_bot:server"
BRIDGE_CONFIG = {
    "hs_token": "secret-token",
    "base_url": "http://connect-v2:29328",
    "bot_mxid": BRIDGE_BOT_MXID,
}


def make_requester(mxid: str):
    requester = MagicMock()
    requester.user.__str__ = lambda _self: mxid
    return requester


def make_handler(bridge=None, config=None):
    # A handler whose archival itself succeeds, for a private non-self-DM room.
    hs = MagicMock()
    api = MagicMock()
    api.get_room_state = AsyncMock(return_value={})
    handler = ArchiveRoomHandler(
        hs, api, {"bridge": BRIDGE_CONFIG} if config is None else config, bridge
    )
    handler.main_store = MagicMock()
    handler.main_store.block_room = AsyncMock()
    handler.main_store.unblock_room = AsyncMock()
    handler.archive_store = MagicMock()
    handler.archive_store.is_self_dm = AsyncMock(return_value=False)
    handler.update_memberships_directly = AsyncMock()
    return handler


class NotifyBridgeTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_tells_the_bridge_who_archived_the_room(self):
        bridge = MagicMock()
        handler = make_handler(bridge)

        await handler.handle_put(ROOM_ID, True, make_requester("@alice:server"))

        bridge.archive_status_changed.assert_called_once_with(
            ROOM_ID, True, "@alice:server"
        )

    async def test_tells_the_bridge_who_unarchived_the_room(self):
        bridge = MagicMock()
        handler = make_handler(bridge)

        await handler.handle_put(ROOM_ID, False, make_requester("@alice:server"))

        bridge.archive_status_changed.assert_called_once_with(
            ROOM_ID, False, "@alice:server"
        )

    async def test_skips_when_requester_is_bridge_bot(self):
        bridge = MagicMock()
        handler = make_handler(bridge)

        await handler.handle_put(ROOM_ID, True, make_requester(BRIDGE_BOT_MXID))

        bridge.archive_status_changed.assert_not_called()

    async def test_notifies_when_requester_is_audiences_bot(self):
        # Auto-archival runs as the audiences bot. That is a genuine v3-originated
        # change and must reach v2, so it must NOT be treated like the bridge bot.
        bridge = MagicMock()
        handler = make_handler(bridge)

        await handler.handle_put(ROOM_ID, True, make_requester("@audiences_bot:server"))

        bridge.archive_status_changed.assert_called_once_with(
            ROOM_ID, True, "@audiences_bot:server"
        )

    async def test_archives_without_a_bridge(self):
        handler = make_handler(bridge=None, config={})

        status, body = await handler.handle_put(
            ROOM_ID, True, make_requester("@alice:server")
        )

        self.assertEqual(body, {"room_id": ROOM_ID, "archive": True})

    async def test_a_self_dm_that_cannot_be_archived_is_not_sent(self):
        bridge = MagicMock()
        handler = make_handler(bridge)
        handler.archive_store.is_self_dm = AsyncMock(return_value=True)

        with self.assertRaises(SynapseError):
            await handler.handle_put(ROOM_ID, True, make_requester("@alice:server"))

        bridge.archive_status_changed.assert_not_called()


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
        handler.bridge = None
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

    async def test_logs_who_requested_the_archive(self):
        handler = self.make_handler(is_self_dm=False)

        with self.assertLogs("connect.archive_rooms.handler", "INFO") as logs:
            await handler.handle_put(
                ROOM_ID, archive=True, requester=make_requester("@alice:server")
            )

        self.assertIn(
            f"Room archive requested for {ROOM_ID} by @alice:server", logs.output[0]
        )

    async def test_logs_who_requested_a_refused_archive(self):
        handler = self.make_handler(is_self_dm=True)

        with self.assertLogs("connect.archive_rooms.handler", "INFO") as logs:
            with self.assertRaises(SynapseError):
                await handler.handle_put(
                    ROOM_ID, archive=True, requester=make_requester("@alice:server")
                )

        self.assertIn("by @alice:server", logs.output[0])


AUDIENCES_BOT = "@audiences_bot:server"


class UpdateMembershipsDirectlyTestSuite(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, members, audiences_bot_id=AUDIENCES_BOT):
        handler = ArchiveRoomHandler.__new__(ArchiveRoomHandler)
        handler.hs = MagicMock()
        handler.bot_ids = [BRIDGE_BOT_MXID, AUDIENCES_BOT]
        handler.audiences_bot_id = audiences_bot_id
        handler.main_store = MagicMock()
        handler.main_store.get_local_users_related_to_room = AsyncMock(
            return_value=[(user_id, Membership.JOIN) for user_id in members]
        )
        handler.room_member_handler = MagicMock()
        handler.room_member_handler.update_membership = AsyncMock(
            return_value=(None, 1)
        )
        handler.room_member_handler.forget = AsyncMock()
        handler.replication = MagicMock()
        handler.replication.wait_for_stream_position = AsyncMock()
        return handler

    def removals(self, handler):
        """Return (remover, target) pairs for every membership change made.

        Args:
            handler (ArchiveRoomHandler): The handler whose mocked membership handler is read.

        Returns:
            list[tuple[str, str]]: The (remover, target) pair of each change, in order.
        """
        return [
            (str(call.kwargs["requester"].user), str(call.kwargs["target"]))
            for call in handler.room_member_handler.update_membership.await_args_list
        ]

    @patch("connect.archive_rooms.handler.is_user_in_room", new_callable=AsyncMock)
    async def test_bot_removes_everyone_including_the_human_requester(
        self, is_user_in_room
    ):
        is_user_in_room.return_value = True
        handler = self.make_handler(
            ["@alice:server", "@bob:server", AUDIENCES_BOT, BRIDGE_BOT_MXID]
        )

        await handler.update_memberships_directly(
            ROOM_ID, create_requester("@alice:server"), archive=True
        )

        # Alice asked for the archival and is removed too, by the bot. Bots stay.
        self.assertEqual(
            self.removals(handler),
            [(AUDIENCES_BOT, "@alice:server"), (AUDIENCES_BOT, "@bob:server")],
        )
        is_user_in_room.assert_awaited_once_with(handler.hs, AUDIENCES_BOT, ROOM_ID)
        forgotten = [
            str(call.args[0])
            for call in handler.room_member_handler.forget.await_args_list
        ]
        self.assertEqual(forgotten, ["@alice:server", "@bob:server"])

    @patch("connect.archive_rooms.handler.is_user_in_room", new_callable=AsyncMock)
    async def test_bridge_bot_requester_stays_in_the_room(self, is_user_in_room):
        # A v2-originated archival is requested by the bridge bot. The audiences
        # bot removes the humans and nobody removes the bridge bot.
        is_user_in_room.return_value = True
        handler = self.make_handler(["@alice:server", BRIDGE_BOT_MXID])

        await handler.update_memberships_directly(
            ROOM_ID, create_requester(BRIDGE_BOT_MXID), archive=True
        )

        self.assertEqual(self.removals(handler), [(AUDIENCES_BOT, "@alice:server")])

    @patch("connect.archive_rooms.handler.is_user_in_room", new_callable=AsyncMock)
    async def test_auto_archive_requested_by_the_bot_removes_the_humans(
        self, is_user_in_room
    ):
        is_user_in_room.return_value = True
        handler = self.make_handler(["@alice:server", AUDIENCES_BOT])

        await handler.update_memberships_directly(
            ROOM_ID, create_requester(AUDIENCES_BOT), archive=True
        )

        self.assertEqual(self.removals(handler), [(AUDIENCES_BOT, "@alice:server")])

    @patch("connect.archive_rooms.handler.is_user_in_room", new_callable=AsyncMock)
    async def test_falls_back_to_the_requester_when_the_bot_is_not_joined(
        self, is_user_in_room
    ):
        is_user_in_room.return_value = False
        handler = self.make_handler(["@alice:server", "@bob:server"])

        with self.assertLogs("connect.archive_rooms.handler", "WARNING") as logs:
            await handler.update_memberships_directly(
                ROOM_ID, create_requester("@alice:server"), archive=True
            )

        # Alice removes Bob and stays: she cannot remove herself.
        self.assertEqual(self.removals(handler), [("@alice:server", "@bob:server")])
        self.assertIn(f"{AUDIENCES_BOT} is not joined to {ROOM_ID}", logs.output[0])

    @patch("connect.archive_rooms.handler.is_user_in_room", new_callable=AsyncMock)
    async def test_falls_back_to_the_requester_when_no_bot_is_configured(
        self, is_user_in_room
    ):
        handler = self.make_handler(
            ["@alice:server", "@bob:server"], audiences_bot_id=None
        )

        await handler.update_memberships_directly(
            ROOM_ID, create_requester("@alice:server"), archive=True
        )

        self.assertEqual(self.removals(handler), [("@alice:server", "@bob:server")])
        is_user_in_room.assert_not_called()

    @patch("connect.archive_rooms.handler.is_user_in_room", new_callable=AsyncMock)
    async def test_unarchiving_never_uses_the_bot(self, is_user_in_room):
        handler = self.make_handler(["@alice:server", "@bob:server"])

        await handler.update_memberships_directly(
            ROOM_ID, create_requester("@alice:server"), archive=False
        )

        self.assertEqual(self.removals(handler), [("@alice:server", "@bob:server")])
        is_user_in_room.assert_not_called()
        handler.room_member_handler.forget.assert_not_called()


if __name__ == "__main__":
    unittest.main()
