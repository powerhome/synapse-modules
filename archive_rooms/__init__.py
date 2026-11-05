"""Archive rooms module."""

import logging
import time
from typing import Literal, Optional, Union

from crontab import CronTab
from pydantic import Field, HttpUrl, SecretStr
from synapse.api.errors import Codes
from synapse.events import EventBase
from synapse.module_api import NOT_SPAM, ModuleApi
from synapse.types import create_requester

from ..base_config import BaseConfig
from .api import ArchiveRoomResource
from .handler import ArchiveRoomHandler
from .store import ArchiveRoomStore

INACTIVITY_THRESHOLD_DAYS = 183


class ArchiveRoomsBridge(BaseConfig):
    """Bridge configuration for archive rooms."""

    hs_token: SecretStr
    base_url: HttpUrl
    impersonator_mxid: str = Field(min_length=1)
    bot_mxid: str = Field(min_length=1)


class Config(BaseConfig):
    """Configuration for archive rooms module."""

    bot_user_ids: list[str] = Field(min_items=1, default_factory=list)
    hs_token: SecretStr
    idp_id: str = Field(min_length=1)
    audiences_services_enabled: bool
    audiences_bot_user_id: str = Field(min_length=1)
    auto_archival_enabled: bool = False
    auto_archival_cron_expression: str = Field(
        default="0 0 * * 1", pattern=r"^(\S+\s+){4}\S+$"
    )
    bridge: Optional[ArchiveRoomsBridge] = None


logger = logging.getLogger(__name__)


class Module:
    """A module that handles the archival of rooms."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        self.api = api
        hs = api._hs
        self.store = ArchiveRoomStore(hs.get_datastores().main)

        api.register_spam_checker_callbacks(
            check_event_for_spam=self.check_event_for_spam
        )

        is_main_process = api.worker_name is None
        if is_main_process:
            logger.info("Registering ArchiveRoomResource on main process")
            self._handler = ArchiveRoomHandler(hs, api, config)
            self._bot_user_id = config["audiences_bot_user_id"]
            self._clock = hs.get_clock()

            api.register_web_resource(
                path="/_connect/archive-rooms",
                resource=ArchiveRoomResource(hs, self._handler, api),
            )

            if config.get("auto_archival_enabled", False):
                self._cron = CronTab(
                    config.get("auto_archival_cron_expression", "0 0 * * 1")
                )
                self._schedule_auto_archive()
            else:
                logger.info("Auto-archival is disabled (room_archival.enabled = false)")

    def _schedule_auto_archive(self) -> None:
        delay = self._cron.next(default_utc=True)
        logger.info(f"Scheduling next auto-archive in {delay} seconds")

        self._clock.call_later(
            delay,
            self.api.run_as_background_process,
            "auto_archive_rooms",
            self._run_auto_archive,
        )

    async def check_event_for_spam(
        self, event: EventBase
    ) -> Union[Literal["NOT_SPAM"], Codes]:
        if await self.store.is_archived(event.room_id):
            # Block ALL events for archived rooms
            return Codes.FORBIDDEN

        return NOT_SPAM

    async def _run_auto_archive(self) -> None:
        self._schedule_auto_archive()

        cutoff_ts_ms = int(
            (time.time() - INACTIVITY_THRESHOLD_DAYS * 24 * 60 * 60) * 1000
        )
        logger.info(
            f"Auto-archive job starting; inactivity cutoff: {INACTIVITY_THRESHOLD_DAYS} days"
        )

        try:
            room_ids = await self.store.get_rooms_eligible_for_auto_archive(
                cutoff_ts_ms
            )
        except Exception as e:
            logger.exception(
                f"Auto-archive job failed while fetching eligible rooms: {e}"
            )
            return

        logger.info(f"Auto-archive job found {len(room_ids)} rooms to archive")
        requester = create_requester(self._bot_user_id)
        archived_count = 0

        for room_id in room_ids:
            try:
                await self._handler.handle_put(
                    room_id, archive=True, requester=requester
                )
                archived_count += 1
                logger.info(f"Auto-archived room {room_id}")
            except Exception as e:
                logger.exception(
                    f"Auto-archive job failed to archive room {room_id}: {e}"
                )

        logger.info(
            f"Auto-archive job complete: {archived_count}/{len(room_ids)} rooms archived"
        )
