"""Module for handling room state events in the Connect service."""

from http import HTTPStatus
from io import BytesIO
from typing import TYPE_CHECKING, Optional, Tuple

from synapse.api.errors import AuthError
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.rest.client.room import RoomStateEventRestServlet
from synapse.types import JsonDict, UserID
from synapse.util import json_encoder

from ..helpers.user import UserHelpers
from .audiences_membership_handler import AudiencesMembershipHandler
from .power_levels_helpers import PowerLevelsHelpers as PowerLevels

if TYPE_CHECKING:
    from synapse.server import HomeServer


class ConnectRoomStateEventRestServlet(RoomStateEventRestServlet):
    """Custom servlet to override default behavior for events (e.g. kick and leave)."""

    def __init__(self, hs: "HomeServer", config: dict):
        """Initializes the servlet.

        Args:
            hs (HomeServer): The HomeServer instance.
            config (dict): Configuration dictionary.
        """
        self.hs = hs
        super().__init__(self.hs)
        self.bots = config.get("bot_user_ids", [])
        self.hs_token = config["hs_token"]
        self.idp_id = f"oidc-{config['idp_id']}"

    async def on_PUT(
        self,
        request: SynapseRequest,
        room_id: str,
        event_type: str,
        state_key: str,
        txn_id: Optional[str] = None,
    ) -> Tuple[int, JsonDict]:
        """
        Directs room 'kick' and 'leave' m.room.member events to the audiences service, so it can manage memberships on Synapse's behalf.

        PUT /client/v3/rooms/{roomId}/state/{eventType}/{stateKey}

        Args:
            request (SynapseRequest): The HTTP request.
            room_id (str): The ID of the room.
            event_type (str): The type of the event.
            state_key (str): The state key of the event.
            txn_id (Optional[str]): The transaction ID, if any.

        Returns:
            Tuple[int, JsonDict]: The HTTP status and response body.

        Raises:
            AuthError: If the membership action is not supported.
        """
        is_main_process = self.hs.config.worker.worker_name is None
        assert is_main_process

        content = parse_json_object_from_request(request)
        membership_action = content.get("membership")
        user_id = content.get("user_id")

        # Revert request.content back to original state.
        # parse_json_object_from_request modifies in place,
        # a forwarded request e.g. super().on_PUT will fail
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

        if event_type != "m.room.member":
            return await super().on_PUT(request, room_id, event_type, state_key, txn_id)

        if membership_action != "leave":
            return await super().on_PUT(request, room_id, event_type, state_key, txn_id)

        if state_key in self.bots:
            return HTTPStatus.OK, {}

        power_levels = PowerLevels(self.hs.get_module_api())
        requester = await self.auth.get_user_by_req(request)
        await power_levels.verify(requester, room_id)

        target = UserID.from_string(state_key)
        audiences = AudiencesMembershipHandler(self.hs, self.hs_token, self.idp_id)
        body = await audiences.update(room_id, [target], membership_action)

        return HTTPStatus.OK, body
