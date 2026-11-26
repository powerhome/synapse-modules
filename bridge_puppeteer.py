"""Bridge puppet handler."""

import logging

from pydantic import HttpUrl, SecretStr
from synapse.module_api import ModuleApi
from synapse.types import UserID

from .base_config import BaseConfig


class Config(BaseConfig):
    """Configuration for bridge puppeteer module."""

    hs_token: SecretStr
    bridge_base_url: HttpUrl


logger = logging.getLogger(__name__)


class Puppeteer:
    """A module that creates puppets on the bridge."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        self.hs = api._hs
        self.token = config["hs_token"].encode("ascii")
        self.bridge_base_url = config["bridge_base_url"]
        self.db_pool = self.hs.get_datastores().main.db_pool

        api.register_account_validity_callbacks(
            on_user_registration=self.on_user_registration
        )

    async def on_user_registration(self, user: str) -> None:
        localpart = UserID.from_string(user).localpart

        data = await self.db_pool.simple_select_one_onecol(
            table="connect.profiles",
            keyvalues={"user_id": localpart},
            retcol="data",
            allow_none=True,
        )

        if data:
            await self._request(user, data)

    async def _request(self, user_mxid: str, data):
        uri = f"{self.bridge_base_url}/_connect/v2/puppets"
        post_json = {
            "remote_user_id": int(data["externalId"]),
            "user_mxid": user_mxid,
        }
        headers = {b"Authorization": [b"Bearer " + self.token]}

        try:
            await self.hs.get_simple_http_client().post_json_get_json(
                uri, post_json, headers=headers
            )
        except Exception as e:
            # TODO improve retry logic
            logger.warning(f"Could not connect to connect-v2, retrying: {e}")
            await self.hs.get_module_api().sleep(60.0)
            await self._request(user_mxid, data)
