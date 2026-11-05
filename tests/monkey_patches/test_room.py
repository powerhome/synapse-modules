"""Tests for room-related monkey patches."""

import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from monkey_patches.room import create_room

config_data = {
    "invite": ["@bob:localhost", "@christine:localhost"],
    "invite_3pid": [],
    "preset": "private_chat",
    "visibility": "private",
    "is_direct": True,
    "initial_state": [
        {
            "type": "m.room.guest_access",
            "state_key": "",
            "content": {"guest_access": "can_join"},
        },
    ],
}


class CreateRoomTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_with_post_processing(self):
        requester_attr = {"user.to_string()": "@alice:localhost"}
        mock_room_creation_handler = AsyncMock(name="room_creation_handler")
        mock_requester = Mock(name="requester", **requester_attr)
        mock_config = MagicMock(name="config", return_value=config_data)

        mock_create_room_original = AsyncMock(return_value=("!1:localhost", None, 0))
        with patch(
            "monkey_patches.room.create_room_original", mock_create_room_original
        ):
            result = await create_room(
                mock_room_creation_handler, mock_requester, mock_config
            )

        self.assertEqual(result, ("!1:localhost", None, 0))

        mock_create_room_original.assert_called_with(
            mock_room_creation_handler,
            mock_requester,
            mock_config,
            ratelimit=False,
            creator_join_profile=None,
        )

    async def test_room_created_with_no_invitees(self):
        no_invitees_config_data = {
            "invite_3pid": [],
            "preset": "private_chat",
            "visibility": "private",
            "initial_state": [
                {
                    "type": "m.room.guest_access",
                    "state_key": "",
                    "content": {"guest_access": "can_join"},
                },
            ],
        }
        requester_attr = {
            "user.to_string()": "@alice:localhost",
            "room_id": "test_room_id",
        }
        mock_room_creation_handler = AsyncMock(name="room_creation_handler")
        mock_requester = Mock(name="requester", **requester_attr)
        mock_config = MagicMock(name="config", return_value=no_invitees_config_data)
        mock_create_room_original = AsyncMock(return_value=("!1:localhost", None, 0))
        with patch(
            "monkey_patches.room.create_room_original", mock_create_room_original
        ):
            result = await create_room(
                mock_room_creation_handler, mock_requester, mock_config
            )

        self.assertEqual(result, ("!1:localhost", None, 0))


if __name__ == "__main__":
    unittest.main()
