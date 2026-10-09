"""Bridge client for Connect room archival."""

import json
from typing import TYPE_CHECKING

from synapse.api.errors import HttpResponseException
from twisted.web.http_headers import Headers

if TYPE_CHECKING:
    from synapse.module_api import ModuleApi


class BridgeClient:
    """Bridge client for Connect room archival."""

    def __init__(self, api: "ModuleApi", config: dict) -> None:
        self.api = api
        self.hs_token = config["hs_token"]
        self.base_url = config["base_url"]

    async def update_archive_status(
        self,
        room_id: str,
        matrix_user_id: str,
        archive: bool,
    ) -> None:
        try:
            uri = f"{self.base_url}/_connect/v2/rooms/{room_id}/archive"
            headers = Headers(
                {b"Authorization": [b"Bearer " + self.hs_token.encode("ascii")]}
            )

            content = {"archive": archive, "matrix_user_id": matrix_user_id}
            data = json.dumps(content).encode("utf-8")
            # http_client.request is used instead of put_json because the remote
            # endpoint may return an empty body, which causes put_json to raise
            # JSONDecodeError.
            await self.api.http_client.request(
                method="PUT", uri=uri, data=data, headers=headers
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e
