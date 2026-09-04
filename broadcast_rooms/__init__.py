"""Broadcast rooms module."""

from typing import Literal, Union

from pydantic import Field
from synapse.api.errors import Codes
from synapse.events import EventBase
from synapse.module_api import NOT_SPAM, ModuleApi

from ..base_config import BaseConfig
from .api import BroadcastRoomResource
from .guard import BroadcastRoomGuard
from .handler import BroadcastRoomHandler
from .store import BroadcastRoomStore


class Config(BaseConfig):
    """Configuration for broadcast rooms module."""

    allowed_localparts: list[str] = Field(min_items=1)


class Module:
    """A module that guards broadcast rooms."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        hs = api._hs
        main_store = hs.get_datastores().main

        store = BroadcastRoomStore(main_store.db_pool)
        handler = BroadcastRoomHandler(store)
        self.guard = BroadcastRoomGuard(main_store, store)

        api.register_spam_checker_callbacks(
            check_event_for_spam=self.check_event_for_spam
        )

        api.register_web_resource(
            path="/_connect/broadcast-rooms",
            resource=BroadcastRoomResource(hs, handler, config["allowed_localparts"]),
        )

    async def check_event_for_spam(
        self, event: EventBase
    ) -> Union[Literal["NOT_SPAM"], Codes]:
        if not await self.guard.can_create_edit_delete_message(event):
            return Codes.FORBIDDEN

        return NOT_SPAM
