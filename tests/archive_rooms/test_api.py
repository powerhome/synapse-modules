"""Tests for the archive rooms servlet authorization."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import SynapseError

from connect.archive_rooms.api import ArchiveRoomServlet

ROOM_ID = "!room:server"
POWER_LEVELS_KEY = ("m.room.power_levels", "")


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


class ValidateUserTestSuite(unittest.IsolatedAsyncioTestCase):
    def make_servlet(
        self, users: dict, members: list[str] | None = None, is_people_convo=False
    ):
        """Build a servlet whose room has the given power levels and members.

        By default every user with a power level is also a joined member.

        Args:
            users (dict): Power level by user id.
            members (list[str] | None): Joined members. Defaults to every user in `users`.
            is_people_convo (bool): Whether the room is a people conversation.

        Returns:
            ArchiveRoomServlet: A servlet wired to mocks for that room.
        """
        servlet = ArchiveRoomServlet.__new__(ArchiveRoomServlet)
        servlet.api = MagicMock()
        servlet.api.get_room_state = AsyncMock(return_value=power_levels_state(users))
        main_store = servlet.api._hs.get_datastores.return_value.main
        main_store.get_users_in_room = AsyncMock(
            return_value=list(users) if members is None else members
        )
        main_store.db_pool.simple_select_one_onecol = AsyncMock(
            return_value=["@a:server", "@b:server"] if is_people_convo else None
        )
        return servlet

    async def assert_forbidden(self, servlet, mxid):
        with self.assertRaises(SynapseError) as ctx:
            await servlet._validate_user(ROOM_ID, make_requester(mxid))

        self.assertEqual(ctx.exception.code, HTTPStatus.FORBIDDEN)

    async def test_allows_joined_requester_with_admin_power_level(self):
        servlet = self.make_servlet({"@admin:server": 100})

        # No exception means the requester is authorized to archive.
        await servlet._validate_user(ROOM_ID, make_requester("@admin:server"))

    async def test_forbids_requester_with_explicit_power_level_below_100(self):
        # Regression: this branch previously constructed the error without
        # raising it, so a member with an explicit sub-100 level could archive.
        servlet = self.make_servlet({"@mod:server": 50})

        await self.assert_forbidden(servlet, "@mod:server")

    async def test_forbids_requester_absent_from_power_levels(self):
        servlet = self.make_servlet(
            {"@admin:server": 100}, members=["@admin:server", "@member:server"]
        )

        await self.assert_forbidden(servlet, "@member:server")

    async def test_forbids_admin_who_is_no_longer_in_the_room(self):
        # Archival removes members without demoting them, so a removed admin
        # keeps power level 100. They must not be able to unarchive the room.
        servlet = self.make_servlet(
            {"@admin:server": 100, "@removed:server": 100}, members=["@admin:server"]
        )

        await self.assert_forbidden(servlet, "@removed:server")

    async def test_forbids_admin_of_a_people_conversation(self):
        servlet = self.make_servlet({"@admin:server": 100}, is_people_convo=True)

        await self.assert_forbidden(servlet, "@admin:server")


if __name__ == "__main__":
    unittest.main()
