"""Routes for archiving rooms."""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import Codes, SynapseError
from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.module_api import ModuleApi, parse_json_object_from_request
from synapse.types import JsonDict, Requester

from .handler import ArchiveRoomHandler

if TYPE_CHECKING:
    from synapse.server import HomeServer


class ArchiveRoomResource(JsonResource):
    def __init__(self, hs: "HomeServer", handler: ArchiveRoomHandler, api: ModuleApi):
        JsonResource.__init__(self, hs, canonical_json=False)
        self.register_servlets(self, hs, handler, api)

    @staticmethod
    def register_servlets(
        resource: HttpServer,
        hs: "HomeServer",
        handler: ArchiveRoomHandler,
        api: ModuleApi,
    ) -> None:
        ArchiveRoomServlet(hs, handler, api).register(resource)


class ArchiveRoomServlet(RestServlet):
    PATTERNS = [re.compile("^/_connect/archive-rooms/(?P<room_id>[^/]*)")]
    CATEGORY = "Archive room requests"

    def __init__(self, hs: "HomeServer", handler: ArchiveRoomHandler, api: ModuleApi):
        super().__init__()
        self.auth = hs.get_auth()
        self.handler = handler
        self.main_store = hs.get_datastores().main
        self.api = api

    async def on_GET(
        self, request: SynapseRequest, room_id: str
    ) -> Tuple[int, JsonDict]:
        await self.auth.get_user_by_req(request)

        return await self.handler.handle_get(room_id)

    async def on_PUT(
        self, request: SynapseRequest, room_id: str
    ) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)
        await self._validate_user(room_id, requester)

        content = parse_json_object_from_request(request)
        archive = content["archive"]

        return await self.handler.handle_put(room_id, archive, requester)

    async def _validate_user(self, room_id: str, requester: Requester):
        required_level = 100

        try:
            room_state = await self.api.get_room_state(
                room_id, [("m.room.power_levels", None)]
            )
            power_levels = room_state[("m.room.power_levels", "")]
            users = power_levels.content["users"]
            requester_power_level = users[str(requester.user)]
        except KeyError:
            raise self._archive_permission_error(requester)

        if requester_power_level < required_level:
            self._archive_permission_error(requester)

    def _archive_permission_error(self, requester: Requester):
        return SynapseError(
            HTTPStatus.FORBIDDEN,
            f"{requester.user.localpart} cannot archive rooms",
            Codes.FORBIDDEN,
        )
