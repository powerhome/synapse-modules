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
    requester.user.__str__ = lambda _self: mxid
    requester.user.localpart = mxid.lstrip("@").split(":")[0]
    return requester


def power_levels_state(users: dict):
    event = MagicMock()
    event.content = {"users": users}
    return {POWER_LEVELS_KEY: event}


class ValidateUserTestSuite(unittest.IsolatedAsyncioTestCase):
    def make_servlet(self, users: dict):
        servlet = ArchiveRoomServlet.__new__(ArchiveRoomServlet)
        servlet.api = MagicMock()
        servlet.api.get_room_state = AsyncMock(return_value=power_levels_state(users))
        return servlet

    async def test_allows_requester_with_admin_power_level(self):
        servlet = self.make_servlet({"@admin:server": 100})

        # No exception means the requester is authorized to archive.
        await servlet._validate_user(ROOM_ID, make_requester("@admin:server"))

    async def test_forbids_requester_with_explicit_power_level_below_100(self):
        # Regression: this branch previously constructed the error without
        # raising it, so a member with an explicit sub-100 level could archive.
        servlet = self.make_servlet({"@mod:server": 50})

        with self.assertRaises(SynapseError) as ctx:
            await servlet._validate_user(ROOM_ID, make_requester("@mod:server"))

        self.assertEqual(ctx.exception.code, HTTPStatus.FORBIDDEN)

    async def test_forbids_requester_absent_from_power_levels(self):
        servlet = self.make_servlet({"@admin:server": 100})

        with self.assertRaises(SynapseError) as ctx:
            await servlet._validate_user(ROOM_ID, make_requester("@member:server"))

        self.assertEqual(ctx.exception.code, HTTPStatus.FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
