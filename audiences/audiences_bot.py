"""Audiences bot."""

import logging
from typing import TYPE_CHECKING, Any, Optional

from twisted.internet import defer

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class AudiencesBot:
    """
    A bot user for the audiences module.

    This class handles the registration and existence check of the audiences bot user.
    """

    def __init__(self, config: dict[str, Any], hs: "HomeServer") -> None:
        self.hs = hs
        self.api = hs.get_module_api()
        self.config = config
        self.bot_mxid = f"@audiences_bot:{hs.hostname}"

    def ensure_registered(self) -> None:
        logger.info("Checking if audiences bot exists...")
        self.api.check_user_exists(self.bot_mxid).addCallback(
            self._on_check_user_exists
        )

    def _on_check_user_exists(self, user: Optional[str]) -> None:
        if not user:
            logger.info("Bot user does not exist. Registering...")
            defer.ensureDeferred(self._register_bot())
        else:
            logger.info("Audiences bot already exists. No need to register.")

    async def _register_bot(self) -> None:
        as_token = self.config["as_token"]
        registration_handler = self.hs.get_registration_handler()
        await registration_handler.appservice_register("audiences_bot", as_token)
        logger.info("Audiences bot registered successfully.")
