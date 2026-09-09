"""Bridge client for Connect notification preferences."""

import json
from typing import TYPE_CHECKING

from synapse.api.errors import HttpResponseException
from synapse.module_api import JsonDict
from twisted.web.http_headers import Headers

if TYPE_CHECKING:
    from synapse.server import HomeServer


class BridgeClient:
    """Bridge client for Connect notification preferences."""

    def __init__(self, hs: "HomeServer", config: dict) -> None:
        self.api = hs.get_module_api()
        self.hs_token = config["hs_token"]
        self.base_url = config["base_url"]

    async def update_preference(
        self,
        matrix_user_id: str,
        matrix_room_id: str,
        content: JsonDict,
    ) -> None:
        try:
            uri = f"{self.base_url}/_connect/v2/notification_preferences/account_data_update"
            headers = Headers(
                {b"Authorization": [b"Bearer " + self.hs_token.encode("ascii")]}
            )

            content["matrix_room_id"] = matrix_room_id
            content["matrix_user_id"] = matrix_user_id
            data = json.dumps(content).encode("utf-8")
            # http_client.request is used instead of put_json because
            # the remote endpoint incorrectly returns HTTP 204 (No Content) with an empty body,
            # causing put_json to raise JSONDecodeError
            await self.api.http_client.request(
                method="PUT", uri=uri, data=data, headers=headers
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e
