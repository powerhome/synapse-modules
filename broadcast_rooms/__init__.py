"""Broadcast rooms module."""

from typing import Literal, Optional, Union

from pydantic import Field
from synapse.api.errors import Codes
from synapse.events import EventBase
from synapse.module_api import NOT_SPAM, ModuleApi

from ..base_config import BaseConfig
from ..v2_bridge import BridgeConfig, notifier_from_config
from .api import BroadcastRoomResource
from .guard import BroadcastRoomGuard
from .handler import DEFAULT_BROADCAST_UPDATED_EVENT_TYPE, BroadcastRoomHandler
from .store import BroadcastRoomStore


class Config(BaseConfig):
    """Configuration for broadcast rooms module."""

    allowed_localparts: list[str] = Field(min_items=1)
    # Feature toggle: whether room admins may change broadcast settings from a
    # Connect v3 client. Off, only the services in allowed_localparts may write.
    admin_writes_enabled: bool = False
    broadcast_updated_event_type: str = Field(
        default=DEFAULT_BROADCAST_UPDATED_EVENT_TYPE, min_length=1
    )
    # Present when Connect v2 is bridged: a person's changes are then sent there.
    bridge: Optional[BridgeConfig] = None


class Module:
    """A module that guards broadcast rooms."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        hs = api._hs
        main_store = hs.get_datastores().main

        store = BroadcastRoomStore(main_store.db_pool)
        handler = BroadcastRoomHandler(
            hs, api, store, config, notifier_from_config(api, config.get("bridge"))
        )
        self.guard = BroadcastRoomGuard(main_store, store)

        api.register_spam_checker_callbacks(
            check_event_for_spam=self.check_event_for_spam
        )

        api.register_web_resource(
            path="/_connect/broadcast-rooms",
            resource=BroadcastRoomResource(
                hs,
                handler,
                api,
                config["allowed_localparts"],
                config.get("admin_writes_enabled", False),
            ),
        )

    async def check_event_for_spam(
        self, event: EventBase
    ) -> Union[Literal["NOT_SPAM"], Codes]:
        if not await self.guard.can_create_edit_delete_message(event):
            return Codes.FORBIDDEN

        return NOT_SPAM
