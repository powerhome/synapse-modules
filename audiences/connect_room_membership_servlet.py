"""Module for room membership servlet."""

from http import HTTPStatus
from io import BytesIO
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import AuthError
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.rest.client.room import RoomMembershipRestServlet
from synapse.types import JsonDict, UserID
from synapse.util import json_encoder

from ..helpers.user import UserHelpers
from .audiences_membership_handler import AudiencesMembershipHandler
from .power_levels_helpers import PowerLevelsHelpers as PowerLevels

if TYPE_CHECKING:
    from synapse.server import HomeServer


class ConnectRoomMembershipRestServlet(RoomMembershipRestServlet):
    """
    Directs room invites to the audiences service, so it can manage memberships on Synapse's behalf.

    POST /client/v3/rooms/{roomId}/invite
    """

    def __init__(self, hs: "HomeServer", config: dict):
        """
        Initializes the ConnectRoomMembershipRestServlet.

        Args:
            hs (HomeServer): The HomeServer instance.
            config (dict): Configuration dictionary.
        """
        self.hs = hs
        self.auth = hs.get_auth()
        self.bots = config.get("bot_user_ids", [])
        self.hs_token = config["hs_token"]
        self.api = hs.get_module_api()
        self.idp_id = f"oidc-{config['idp_id']}"

    async def on_POST(
        self,
        request: SynapseRequest,
        room_id: str,
        membership_action: str,
    ) -> Tuple[int, JsonDict]:
        """
        Handles POST requests for room membership actions.

        Args:
            request (SynapseRequest): The HTTP request.
            room_id (str): The ID of the room.
            membership_action (str): The membership action to perform.

        Returns:
            Tuple[int, JsonDict]: The HTTP status and response body.

        Raises:
            AuthError: If the membership action is not supported.
        """
        is_main_process = self.hs.config.worker.worker_name is None
        assert is_main_process

        if membership_action in ["ban", "unban", "join"]:
            raise AuthError(403, f"{membership_action} not supported")

        content = parse_json_object_from_request(request, allow_empty_body=True)
        user_id = content.get("user_id")

        # Revert request.content back to original state.
        # parse_json_object_from_request modifies in place,
        # a forwarded request e.g. super().on_POST will fail
        # since parse_json_object_from_request is called again
        content_json = json_encoder.encode(content)
        content_json_bytes = content_json.encode("utf-8")
        request.content = BytesIO(content_json_bytes)

        # Block all membership events other than leave for deactivated users
        if membership_action != "leave" and user_id:
            if await UserHelpers.is_user_deactivated(
                self.hs.get_datastores().main, user_id
            ):
                raise AuthError(403, "Only leave events allowed on deactivated user")

        # Prevent leaving private rooms
        public_room = await self._is_public(room_id)
        if membership_action == "leave" and not public_room:
            raise AuthError(403, f"{membership_action} not supported on private rooms")

        if user_id in self.bots:
            return HTTPStatus.OK, {}

        power_levels = PowerLevels(self.api)
        requester = await self.auth.get_user_by_req(request)

        if membership_action == "leave" and public_room:
            levels = await power_levels.get(room_id)
            users = levels.content["users"]
            requesting_user_id = requester.user.to_string()
            # We do not want the room to have only bot user admins
            greater_than_one_admin_in_room = self._more_than_one_human_admin(users)
            if (
                requesting_user_id in users
                and users[requesting_user_id] == 100
                and requesting_user_id not in self.bots
                and not greater_than_one_admin_in_room
            ):
                return HTTPStatus.BAD_REQUEST, {
                    "error": "Admins can not leave public rooms, if they're the sole remaining human admin."
                }
            else:
                return await self._notify_audiences(
                    room_id, requester.user, membership_action
                )
        else:
            target = UserID.from_string(user_id)
            await power_levels.verify(requester, room_id)

            return await self._notify_audiences(room_id, target, membership_action)

    async def _is_public(self, room_id: str) -> bool:
        """
        Checks if room is public.

        Args:
            room_id (str): The ID of the room.

        Returns:
            bool: True if room is public, False otherwise.
        """
        join_rules_state = await self.api.get_room_state(
            room_id, [("m.room.join_rules", None)]
        )
        join_rules = join_rules_state.get(("m.room.join_rules", ""))
        assert (
            join_rules is not None
        ), f"No join rules found in room state {join_rules_state}"
        return join_rules.content["join_rule"] == "public"

    def _more_than_one_human_admin(self, users_power_levels: dict) -> bool:
        """
        Checks there are more than one non-bot admins in the room.

        Args:
            users_power_levels (dict): The power levels of all the users in the room

        Returns:
            bool: True if only one human admin, False otherwise.
        """
        count = 0
        for user, power_level in users_power_levels.items():
            if power_level == 100 and user not in self.bots:
                count += 1
        return count > 1

    async def _notify_audiences(
        self, room_id: str, user_id: UserID, membership_action: str
    ) -> Tuple[int, JsonDict]:
        """
        Notifies the audiences service of a room membership action.

        Args:
            room_id (str): The ID of the room.
            user_id (UserID): The matrix id of the user
            membership_action (str): The membership action to perform.

        Returns:
            JsonDict: The response body.
        """
        audiences = AudiencesMembershipHandler(self.hs, self.hs_token, self.idp_id)
        body = await audiences.update(room_id, [user_id], membership_action)
        return HTTPStatus.OK, body
