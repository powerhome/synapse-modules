"""HTTP client for the Connect v2 bridge's v3 -> v2 webhooks."""

import json
from typing import TYPE_CHECKING
from urllib.parse import quote

from synapse.logging.context import make_deferred_yieldable
from twisted.web.client import readBody
from twisted.web.http_headers import Headers

if TYPE_CHECKING:
    from synapse.http.client import SimpleHttpClient


class BridgeRefusedError(Exception):
    """The bridge answered a 4xx: sending the same change again cannot succeed."""

    def __init__(self, status: int, body: str):
        super().__init__(f"bridge answered {status}: {body}")
        self.status = status


class BridgeUnavailableError(Exception):
    """The bridge answered a 5xx: the change may succeed later."""


class BridgeClient:
    """Sends v3 room changes to the Connect v2 bridge, which applies them in Connect v2.

    Each call carries the room's full state for one setting, so sending it again is safe.
    """

    def __init__(self, http_client: "SimpleHttpClient", hs_token: str, base_url: str):
        self.http_client = http_client
        self.hs_token = hs_token
        self.base_url = base_url

    async def update_archive_status(
        self, room_id: str, archive: bool, initiator: str
    ) -> None:
        """Archive or unarchive the room in Connect v2.

        Args:
            room_id: The room whose archive status changed.
            archive: Whether the room is now archived.
            initiator: The v3 user who made the change, for the bridge's logs.
        """
        await self._put(
            f"/_connect/v2/rooms/{quote(room_id, safe='')}/archive",
            {"archive": archive, "matrix_user_id": initiator},
        )

    async def update_broadcasters(
        self, room_id: str, broadcasters: list[str], initiator: str
    ) -> None:
        """Replace the room's broadcasters in Connect v2.

        Args:
            room_id: The room whose broadcasters changed.
            broadcasters: The room's broadcasters; empty when it is no longer a broadcast room.
            initiator: The v3 user who made the change, for the bridge's logs.
        """
        await self._put(
            f"/_connect/v2/rooms/{quote(room_id, safe='')}/broadcasters",
            {"broadcasters": broadcasters, "matrix_user_id": initiator},
        )

    async def _put(self, path: str, content: dict) -> None:
        """PUT `content` to the bridge.

        When the bridge cannot be reached or does not answer in time, whatever
        the HTTP client raises is let through.

        Args:
            path: The webhook's path on the bridge.
            content: The JSON body.

        Raises:
            BridgeRefusedError: When the bridge answers a 4xx.
            BridgeUnavailableError: When the bridge answers a 5xx.
        """
        headers = Headers(
            {
                b"Authorization": [b"Bearer " + self.hs_token.encode("ascii")],
                b"Content-Type": [b"application/json"],
            }
        )
        # http_client.request is used instead of put_json because the bridge answers
        # with a plain-text body, which put_json would fail to decode.
        response = await self.http_client.request(
            method="PUT",
            uri=f"{self.base_url}{path}",
            data=json.dumps(content).encode("utf-8"),
            headers=headers,
        )
        body = (await make_deferred_yieldable(readBody(response))).decode(
            "utf-8", errors="replace"
        )

        if 200 <= response.code < 300:
            return
        if 400 <= response.code < 500:
            raise BridgeRefusedError(response.code, body)
        raise BridgeUnavailableError(f"bridge answered {response.code}: {body}")
