"""Tests for the broadcast rooms servlet authorization."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.api.errors import SynapseError

from connect.broadcast_rooms.api import BroadcastRoomServlet

ROOM_ID = "!room:server"
POWER_LEVELS_KEY = ("m.room.power_levels", "")
ALLOWED_LOCALPARTS = ["bridge", "connect_bot"]


def make_requester(mxid: str):
    requester = MagicMock()
    requester.user.to_string.return_value = mxid
    requester.user.__str__ = lambda _self: mxid
    requester.user.localpart = mxid.lstrip("@").split(":")[0]
    return requester


def power_levels_state(users: dict):
    event = MagicMock()
    event.content = {"users": users}
    return {POWER_LEVELS_KEY: event}


def make_servlet(
    room_state: dict,
    admin_writes_enabled: bool = True,
    members: list[str] | None = None,
    is_people_convo: bool = False,
):
    """Build a servlet whose room has the given state and members.

    By default every user with a power level is also a joined member.

    Args:
        room_state: The room's current state, keyed by (type, state_key).
        admin_writes_enabled: Whether the admin_writes_enabled toggle is on.
        members: The room's joined members; defaults to the power-level users.
        is_people_convo: Whether the room is a DM or group conversation.

    Returns:
        A BroadcastRoomServlet with mocked collaborators.
    """
    if members is None:
        power_levels = room_state.get(POWER_LEVELS_KEY)
        members = list(power_levels.content.get("users", {})) if power_levels else []

    servlet = BroadcastRoomServlet.__new__(BroadcastRoomServlet)
    servlet.api = MagicMock()
    servlet.api.get_room_state = AsyncMock(return_value=room_state)
    main_store = servlet.api._hs.get_datastores.return_value.main
    main_store.get_users_in_room = AsyncMock(return_value=members)
    main_store.db_pool.simple_select_one_onecol = AsyncMock(
        return_value=["@a:server", "@b:server"] if is_people_convo else None
    )
    servlet.allowed_localparts = ALLOWED_LOCALPARTS
    servlet.admin_writes_enabled = admin_writes_enabled
    servlet.auth = MagicMock()
    servlet.handler = MagicMock()
    servlet.handler.handle_put = AsyncMock(return_value=(HTTPStatus.OK, {}))
    servlet.handler.handle_delete = AsyncMock(return_value=(HTTPStatus.OK, {}))
    return servlet


class ValidateUserTestSuite(unittest.IsolatedAsyncioTestCase):
    async def assert_forbidden(self, servlet, mxid: str):
        with self.assertRaises(SynapseError) as ctx:
            await servlet._validate_user(ROOM_ID, make_requester(mxid))

        self.assertEqual(ctx.exception.code, HTTPStatus.FORBIDDEN)

    async def test_allows_requester_with_admin_power_level(self):
        servlet = make_servlet(power_levels_state({"@admin:server": 100}))

        # No exception means the requester may change the room's broadcasters.
        await servlet._validate_user(ROOM_ID, make_requester("@admin:server"))

    async def test_allows_the_bridge_and_the_bot_without_reading_power_levels(self):
        servlet = make_servlet(power_levels_state({}))

        await servlet._validate_user(ROOM_ID, make_requester("@bridge:server"))
        await servlet._validate_user(ROOM_ID, make_requester("@connect_bot:server"))

        servlet.api.get_room_state.assert_not_called()

    async def test_forbids_requester_with_power_level_below_100(self):
        servlet = make_servlet(power_levels_state({"@mod:server": 50}))

        await self.assert_forbidden(servlet, "@mod:server")

    async def test_forbids_requester_absent_from_power_levels(self):
        servlet = make_servlet(power_levels_state({"@admin:server": 100}))

        await self.assert_forbidden(servlet, "@member:server")

    async def test_forbids_when_power_levels_have_no_users(self):
        event = MagicMock()
        event.content = {}
        servlet = make_servlet({POWER_LEVELS_KEY: event})

        await self.assert_forbidden(servlet, "@member:server")

    async def test_forbids_when_room_has_no_power_levels(self):
        # Also what an unknown room looks like: nobody is joined to it.
        servlet = make_servlet({})

        await self.assert_forbidden(servlet, "@member:server")

    async def test_forbids_admin_who_is_no_longer_in_the_room(self):
        # Leaving demotes an admin, but being banned, or removed from an
        # archived room, does not: they keep power level 100.
        servlet = make_servlet(
            power_levels_state({"@admin:server": 100, "@removed:server": 100}),
            members=["@admin:server"],
        )

        await self.assert_forbidden(servlet, "@removed:server")

    async def test_forbids_admin_of_a_people_conversation(self):
        servlet = make_servlet(
            power_levels_state({"@admin:server": 100}), is_people_convo=True
        )

        await self.assert_forbidden(servlet, "@admin:server")


class AdminWritesToggleTestSuite(unittest.IsolatedAsyncioTestCase):
    """The feature toggle that keeps admin writes off in production."""

    async def test_off__forbids_an_admin_without_reading_power_levels(self):
        servlet = make_servlet(
            power_levels_state({"@admin:server": 100}), admin_writes_enabled=False
        )

        with self.assertRaises(SynapseError) as ctx:
            await servlet._validate_user(ROOM_ID, make_requester("@admin:server"))

        self.assertEqual(ctx.exception.code, HTTPStatus.FORBIDDEN)
        self.assertEqual(ctx.exception.msg, "Changing broadcast rooms is not enabled")
        servlet.api.get_room_state.assert_not_called()

    async def test_off__still_allows_the_bridge_and_the_bot(self):
        servlet = make_servlet(power_levels_state({}), admin_writes_enabled=False)

        await servlet._validate_user(ROOM_ID, make_requester("@bridge:server"))
        await servlet._validate_user(ROOM_ID, make_requester("@connect_bot:server"))

    async def test_on__allows_an_admin(self):
        servlet = make_servlet(
            power_levels_state({"@admin:server": 100}), admin_writes_enabled=True
        )

        await servlet._validate_user(ROOM_ID, make_requester("@admin:server"))


@patch(
    "connect.broadcast_rooms.api.parse_json_object_from_request",
    return_value={"broadcasters": ["@alice:server"]},
)
class WriteRequestsTestSuite(unittest.IsolatedAsyncioTestCase):
    def make_servlet_for(self, mxid: str, users: dict, members=None):
        servlet = make_servlet(power_levels_state(users), members=members)
        self.requester = make_requester(mxid)
        servlet.auth.get_user_by_req = AsyncMock(return_value=self.requester)
        return servlet

    async def test_put_passes_the_requester_to_the_handler(self, _parse):
        servlet = self.make_servlet_for("@admin:server", {"@admin:server": 100})

        code, _ = await servlet.on_PUT(MagicMock(), ROOM_ID)

        self.assertEqual(code, HTTPStatus.OK)
        servlet.handler.handle_put.assert_awaited_once_with(
            ROOM_ID, {"broadcasters": ["@alice:server"]}, self.requester
        )

    async def test_put_by_a_non_admin_never_reaches_the_handler(self, _parse):
        servlet = self.make_servlet_for("@member:server", {"@admin:server": 100})

        with self.assertRaises(SynapseError):
            await servlet.on_PUT(MagicMock(), ROOM_ID)

        servlet.handler.handle_put.assert_not_called()

    async def test_delete_passes_the_requester_to_the_handler(self, _parse):
        servlet = self.make_servlet_for("@admin:server", {"@admin:server": 100})

        code, _ = await servlet.on_DELETE(MagicMock(), ROOM_ID)

        self.assertEqual(code, HTTPStatus.OK)
        servlet.handler.handle_delete.assert_awaited_once_with(ROOM_ID, self.requester)

    async def test_delete_by_a_non_admin_never_reaches_the_handler(self, _parse):
        servlet = self.make_servlet_for("@member:server", {"@admin:server": 100})

        with self.assertRaises(SynapseError):
            await servlet.on_DELETE(MagicMock(), ROOM_ID)

        servlet.handler.handle_delete.assert_not_called()

    async def test_put_by_an_admin_no_longer_in_the_room_never_reaches_the_handler(
        self, _parse
    ):
        servlet = self.make_servlet_for(
            "@removed:server", {"@removed:server": 100}, members=[]
        )

        with self.assertRaises(SynapseError):
            await servlet.on_PUT(MagicMock(), ROOM_ID)

        servlet.handler.handle_put.assert_not_called()

    async def test_delete_by_an_admin_no_longer_in_the_room_never_reaches_the_handler(
        self, _parse
    ):
        servlet = self.make_servlet_for(
            "@removed:server", {"@removed:server": 100}, members=[]
        )

        with self.assertRaises(SynapseError):
            await servlet.on_DELETE(MagicMock(), ROOM_ID)

        servlet.handler.handle_delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
