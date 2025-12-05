"""Routes for broadcast rooms."""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import Codes, SynapseError
from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.types import JsonDict

from .handler import BroadcastRoomHandler

if TYPE_CHECKING:
    from synapse.server import HomeServer


class BroadcastRoomResource(JsonResource):
    def __init__(
        self,
        hs: "HomeServer",
        handler: BroadcastRoomHandler,
        allowed_localparts: list[str],
    ):
        JsonResource.__init__(self, hs, canonical_json=False)
        self.register_servlets(self, hs, handler, allowed_localparts)

    @staticmethod
    def register_servlets(
        resource: HttpServer,
        hs: "HomeServer",
        handler: BroadcastRoomHandler,
        allowed_localparts: list[str],
    ) -> None:
        BroadcastRoomListServlet(hs, handler).register(resource)
        BroadcastRoomServlet(hs, handler, allowed_localparts).register(resource)


class BroadcastRoomListServlet(RestServlet):
    PATTERNS = [re.compile("^/_connect/broadcast-rooms$")]
    CATEGORY = "Broadcast room requests"

    def __init__(self, hs: "HomeServer", handler: BroadcastRoomHandler):
        super().__init__()
        self.auth = hs.get_auth()
        self.handler = handler

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        await self.auth.get_user_by_req(request)

        return await self.handler.handle_all()


class BroadcastRoomServlet(RestServlet):
    PATTERNS = [re.compile("^/_connect/broadcast-rooms/(?P<room_id>[^/]*)")]
    CATEGORY = "Broadcast room requests"

    def __init__(
        self,
        hs: "HomeServer",
        handler: BroadcastRoomHandler,
        allowed_localparts: list[str],
    ):
        super().__init__()
        self.auth = hs.get_auth()
        self.handler = handler
        self.allowed_localparts = allowed_localparts

    async def on_GET(
        self, request: SynapseRequest, room_id: str
    ) -> Tuple[int, JsonDict]:
        await self.auth.get_user_by_req(request)

        return await self.handler.handle_get(room_id)

    async def on_PUT(
        self, request: SynapseRequest, room_id: str
    ) -> Tuple[int, JsonDict]:
        await self._validate_user(request)

        content = parse_json_object_from_request(request)
        return await self.handler.handle_put(room_id, content)

    async def on_DELETE(
        self, request: SynapseRequest, room_id: str
    ) -> Tuple[int, JsonDict]:
        await self._validate_user(request)

        return await self.handler.handle_delete(room_id)

    async def _validate_user(self, request: SynapseRequest):
        requester = await self.auth.get_user_by_req(request)
        # TODO until we implement permissions, we only let the bridge modify broadcast rooms
        # when an appservice includes "?user_id=<mxid>" in the broadcast rooms URL,
        # Synapse will consider that user to be the requester instead
        if requester.user.localpart not in self.allowed_localparts:
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                f"{requester.user.localpart} cannot modify broadcast rooms",
                Codes.FORBIDDEN,
            )
