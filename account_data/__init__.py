"""Bookmarks module."""

import json
from typing import Optional

from pydantic import HttpUrl, SecretStr
from synapse.api.errors import HttpResponseException
from synapse.module_api import JsonDict, ModuleApi

from ..base_config import BaseConfig
from ..custom_types import CUSTOM_EVENT_TYPE


class Config(BaseConfig):
    """Configuration for bookmarks module."""

    hs_token: SecretStr
    ordering_for_self_event_type: CUSTOM_EVENT_TYPE
    bridge_base_url: HttpUrl


class Module:
    """A module that handles bookmarks."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        self.api = api
        self.hs_token = config["hs_token"]
        self.ordering_for_self_event_type = config["ordering_for_self_event_type"]
        self.ordering_for_self_metadata_tag = (
            f"{self.ordering_for_self_event_type}_metadata"
        )
        self.bridge_base_url = config["bridge_base_url"]

        self.api.register_account_data_callbacks(
            on_account_data_updated=self.send_bookmark_update_to_bridge,
        )

    async def send_bookmark_update_to_bridge(
        self,
        user_id: str,
        room_id: Optional[str],
        account_data_type: str,
        content: JsonDict,
    ) -> None:
        try:
            if (
                account_data_type == self.ordering_for_self_event_type
                and content.get("origin") != "v2"
            ):
                uri = f"{self.bridge_base_url}/_connect/v2/bookmarks/account_data_update?user_id={user_id}"
                headers = {
                    b"Authorization": [b"Bearer " + self.hs_token.encode("ascii")]
                }
                metadata = await self.api.account_data_manager.get_global(
                    user_id, self.ordering_for_self_metadata_tag
                )
                if metadata:
                    version = metadata.get("version", 0)

                    args = {
                        "account_data_type": account_data_type,
                        "bookmarks": content.get(self.ordering_for_self_event_type),
                        "version": version,
                    }
                    await self.api.http_client.put_json(
                        uri, json_body=json.dumps(args), headers=headers
                    )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e
