"""Audiences module."""

import logging
from typing import Any

from pydantic import Field, SecretStr
from synapse.module_api import ModuleApi

from connect.custom_types import CUSTOM_EVENT_TYPE

from ..base_config import BaseConfig
from .api import AudiencesResource
from .audiences_bot import AudiencesBot
from .room_creation_listener import RoomCreationListener


class Config(BaseConfig):
    """Configuration for audiences module."""

    hs_token: SecretStr
    as_token: SecretStr
    services_enabled: bool
    audiences_bot_user_id: str = Field(min_length=1)
    bridge_bot_user_id: str = Field(min_length=1)
    bot_user_ids: list[str] = Field(min_items=1, default_factory=list)
    idp_id: str = Field(min_length=1)
    room_fully_created_event_type: CUSTOM_EVENT_TYPE
    audience_context_updated_event_type: CUSTOM_EVENT_TYPE


logger = logging.getLogger(__name__)


class Module:
    """A module that handles audiences."""

    def __init__(self, config: dict[str, Any], api: ModuleApi) -> None:
        hs = api._hs
        AudiencesBot(config, hs).ensure_registered()
        if not config.get("services_enabled"):
            logger.info("Not initializing RoomCreationListener or audiences resources")
            return

        Config.model_validate(config)

        is_worker = api.worker_name is not None
        if is_worker:
            logger.info(
                f"registering audiences web resources on worker {api.worker_name}"
            )
            api.register_web_resource(
                path="/audiences",
                resource=AudiencesResource(hs, config),
            )

        else:
            logger.info("Initializing RoomCreationListener on main process")
            room_creation_listener = RoomCreationListener(api, config)
            room_creation_listener.register_third_party_rules_callbacks()
