"""Module for RoomCreationListener class."""

import logging
from typing import Any

from synapse.module_api import EventBase, ModuleApi
from synapse.types import Requester, UserID

from .audiences_membership_handler import AudiencesMembershipHandler

logger = logging.getLogger(__name__)


class RoomCreationListener:
    """Listens for newly created rooms, and applies audiences related changes."""

    def __init__(self, api: ModuleApi, config: dict[str, Any]):
        self.api = api
        self.hs = api._hs
        self.hs_token = config["hs_token"]
        self.bot_user_ids = config["bot_user_ids"]
        self.audiences_bot_user_id = config["audiences_bot_user_id"]
        self.bridge_bot_user_id = config["bridge_bot_user_id"]
        self.idp_id = f"oidc-{config['idp_id']}"
        self.room_fully_created_event_type = config["room_fully_created_event_type"]
        assert (
            api.worker_name is None
        ), "RoomCreationListener should only be used on the main process"

    def register_third_party_rules_callbacks(self) -> None:
        self.api.register_third_party_rules_callbacks(
            on_create_room=self._on_create_room,
            on_new_event=self._on_new_event,
        )

    async def _on_create_room(
        self, requester: Requester, request_content: dict, is_requester_admin: bool
    ) -> None:
        if request_content.get("is_direct"):
            logger.info(
                f"RoomCreationListener _on_create_room: Skipping room creation for {requester.user} because it is a people conversation"
            )
            return

        if "invite" not in request_content:
            request_content["invite"] = []

        power_level_override = request_content.setdefault(
            "power_level_content_override", {}
        )
        user_power_levels = power_level_override.setdefault("users", {})
        invites = request_content["invite"]

        invites.append(self.audiences_bot_user_id)
        user_power_levels[self.audiences_bot_user_id] = 100

        requester_user_id = requester.user.to_string()
        user_power_levels[requester_user_id] = 100

        if not requester_user_id == self.bridge_bot_user_id:
            invites.append(self.bridge_bot_user_id)
            user_power_levels[self.bridge_bot_user_id] = 100

    async def _on_new_event(self, event: EventBase, *args: Any) -> None:
        # This waits for the room_fully_created_event_type event because that happens after "m.room.create".
        if event.is_state() and event.type == self.room_fully_created_event_type:
            room_id = event.room_id
            if event.content.get("is_direct") is True:
                logger.info(
                    f"RoomCreationListener _on_new_event: Skipping room {room_id} because it is a direct chat"
                )
                return

            logger.info(
                f"RoomCreationListener _on_new_event: Processing room {room_id}"
            )

            room_creator = UserID.from_string(event.user_id)
            audiences = AudiencesMembershipHandler(self.hs, self.hs_token, self.idp_id)
            await audiences.update(room_id, [room_creator], "invite")
