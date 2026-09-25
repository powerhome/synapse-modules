"""Rooms module."""

import logging

from synapse.module_api import ModuleApi
from synapse.types import JsonDict, UserID

from connect.base_config import BaseConfig
from connect.custom_types import CUSTOM_EVENT_TYPE

from .monkey_patches import after_create_room_callbacks


class Config(BaseConfig):
    """Configuration for rooms module."""

    room_fully_created_event_type: CUSTOM_EVENT_TYPE


logger = logging.getLogger(__name__)


class Module:
    """A class containing room logic not implemented by Synapse."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        self.api = api
        self.room_fully_created_event_type = config["room_fully_created_event_type"]
        is_worker = api.worker_name is not None
        if is_worker:
            logger.info(
                f"Rooms: not initializing module on worker process: {api.worker_name}"
            )
            return
        else:
            logger.info("Rooms: initializing module on main process")

        after_create_room_callbacks.append(self.after_create_room)

    async def after_create_room(
        self, user_id: UserID, room_id: str, config: JsonDict
    ) -> None:
        content = {}
        is_direct = config.get("is_direct", None)
        if is_direct:
            content["is_direct"] = is_direct
            content["invited_member_count"] = 1 + len(config.get("invite", []))
        else:
            content["name"] = config.get("name", None)
            content["preset"] = config.get("preset", None)
            content["visibility"] = config.get("visibility", None)

        event = await self.api.create_and_send_event_into_room(
            {
                "type": self.room_fully_created_event_type,
                "room_id": room_id,
                "sender": user_id.to_string(),
                "state_key": "",
                "content": content,
            }
        )
        logger.info(f"{event.user_id} created room {event.room_id}")
