"""Notification preferences module."""

import logging
from typing import Any, Optional

from pydantic import Field, HttpUrl, SecretStr
from synapse.module_api import ModuleApi

from connect.custom_types import CUSTOM_EVENT_TYPE

from ..base_config import BaseConfig
from .notification_preferences_api import NotificationPreferencesResource
from .room_membership_listener import RoomMembershipListener


class NotificationPreferencesBridge(BaseConfig):
    """Bridge configuration for notification preferences."""

    hs_token: SecretStr
    base_url: HttpUrl


class Config(BaseConfig):
    """Configuration for notification preferences module."""

    bridge: Optional[NotificationPreferencesBridge] = None
    bot_user_ids: list[str] = Field(min_items=1, default_factory=list)
    notification_preference_event_type: CUSTOM_EVENT_TYPE
    room_fully_created_event_type: CUSTOM_EVENT_TYPE


logger = logging.getLogger(__name__)


class Module:
    """A module that handles the per-room notification preferences."""

    def __init__(self, config: dict[str, Any], api: ModuleApi) -> None:
        Config.model_validate(config)
        is_worker = api.worker_name is not None
        if is_worker:
            logger.info("Not initializing module on worker process")
            return

        logger.info("Initializing module on main process")
        logger.info("Initializing RoomMembershipListener")
        RoomMembershipListener(api, config).register_third_party_rules_callbacks()

        logger.info("Registering NotificationPreferencesResource")
        api.register_web_resource(
            path="/notification_preferences",
            resource=NotificationPreferencesResource(api._hs, config),
        )
