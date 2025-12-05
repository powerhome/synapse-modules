"""Archive rooms module."""

import logging
from typing import Literal, Union

from pydantic import Field, SecretStr
from synapse.api.errors import Codes
from synapse.events import EventBase
from synapse.module_api import NOT_SPAM, ModuleApi

from ..base_config import BaseConfig
from .api import ArchiveRoomResource
from .handler import ArchiveRoomHandler
from .store import ArchiveRoomStore


class Config(BaseConfig):
    """Configuration for archive rooms module."""

    bot_user_ids: list[str] = Field(min_items=1, default_factory=list)
    hs_token: SecretStr
    idp_id: str = Field(min_length=1)
    audiences_services_enabled: bool
    audiences_bot_user_id: str = Field(min_length=1)


logger = logging.getLogger(__name__)


class Module:
    """A module that handles the archival of rooms."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        hs = api._hs
        self.store = ArchiveRoomStore(hs.get_datastores().main)

        api.register_spam_checker_callbacks(
            check_event_for_spam=self.check_event_for_spam
        )

        is_main_process = api.worker_name is None
        if is_main_process:
            logger.info("Registering ArchiveRoomResource on main process")
            handler = ArchiveRoomHandler(hs, api, config)

            api.register_web_resource(
                path="/_connect/archive-rooms",
                resource=ArchiveRoomResource(hs, handler, api),
            )

    async def check_event_for_spam(
        self, event: EventBase
    ) -> Union[Literal["NOT_SPAM"], Codes]:
        if await self.store.is_archived(event.room_id):
            # Block ALL events for archived rooms
            return Codes.FORBIDDEN

        return NOT_SPAM
