"""Purge module."""

import logging

from crontab import CronTab
from pydantic import Field
from synapse.module_api import ModuleApi

from connect.base_config import BaseConfig


class Config(BaseConfig):
    """Configuration for purge module."""

    cron_expression: str = Field(pattern=r"^(\S+\s+){4}\S+$")


logger = logging.getLogger(__name__)


class Module:
    """A module that purges old messages on a cron schedule."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        expression = config["cron_expression"]
        self.cron = CronTab(expression)
        self.api = api

        hs = api._hs
        self.pagination_handler = hs.get_pagination_handler()

        # As in the built-in mechanism, the purge functionality only runs on the main process:
        # https://github.com/matrix-org/synapse/blob/v1.98.0/synapse/handlers/pagination.py#L107-L119
        is_retention_enabled = hs.config.retention.retention_enabled
        is_main_process = hs.config.worker.worker_app is None
        if is_retention_enabled and is_main_process:
            self._schedule_purge()

    def _schedule_purge(self):
        delay = self.cron.next(default_utc=True)
        logger.info(f"Scheduling next purge in {delay} seconds")

        self.pagination_handler.clock.call_later(
            delay,
            self.api.run_as_background_process,
            "purge_history_for_rooms_in_range",
            self._purge,
        )

    async def _purge(self):
        self._schedule_purge()

        # Passing only "None" arguments mimics only setting the interval in retention.purge_jobs,
        # which ensures that the purge functionality affects all messages that are old enough.
        await self.pagination_handler.purge_history_for_rooms_in_range(None, None)
