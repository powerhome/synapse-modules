"""Routes for broadcast rooms."""

import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import AuthError, Codes, SynapseError
from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.module_api import ModuleApi, parse_json_object_from_request
from synapse.types import JsonDict, Requester

from ..audiences.power_levels_helpers import PowerLevelsHelpers
from .handler import BroadcastRoomHandler

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class BroadcastRoomResource(JsonResource):
    def __init__(
        self,
        hs: "HomeServer",
        handler: BroadcastRoomHandler,
        api: ModuleApi,
        allowed_localparts: list[str],
        admin_writes_enabled: bool = False,
    ):
        JsonResource.__init__(self, hs, canonical_json=False, extract_context=True)
        self.register_servlets(
            self, hs, handler, api, allowed_localparts, admin_writes_enabled
        )

    @staticmethod
    def register_servlets(
        resource: HttpServer,
        hs: "HomeServer",
        handler: BroadcastRoomHandler,
        api: ModuleApi,
        allowed_localparts: list[str],
        admin_writes_enabled: bool = False,
    ) -> None:
        BroadcastRoomListServlet(hs, handler).register(resource)
        BroadcastRoomServlet(
            hs, handler, api, allowed_localparts, admin_writes_enabled
        ).register(resource)


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
        api: ModuleApi,
        allowed_localparts: list[str],
        admin_writes_enabled: bool = False,
    ):
        super().__init__()
        self.auth = hs.get_auth()
        self.handler = handler
        self.api = api
        self.allowed_localparts = allowed_localparts
        self.admin_writes_enabled = admin_writes_enabled

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
        return await self.handler.handle_put(room_id, content, requester)

    async def on_DELETE(
        self, request: SynapseRequest, room_id: str
    ) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)
        await self._validate_user(room_id, requester)

        return await self.handler.handle_delete(room_id, requester)

    async def _validate_user(self, room_id: str, requester: Requester) -> None:
        """Let the bridge, the bot and the room's admins change broadcast settings.

        The bridge and the bot are recognised by localpart: when an appservice
        includes "?user_id=<mxid>" in the URL, Synapse considers that user to be
        the requester. Anyone else is only let through while the
        admin_writes_enabled feature toggle is on, and must pass the same check
        as archiving a room: joined to the room, power level 100, and the room
        is not a DM or group conversation. Membership matters because an admin
        who is banned, or removed from an archived room, keeps power level 100.

        Args:
            room_id: The room whose broadcast settings are being changed.
            requester: The user making the request.

        Raises:
            SynapseError: 403 when the admin_writes_enabled toggle is off.
            AuthError: 403 when the requester is not a joined admin of a non-DM room.
        """
        if requester.user.localpart in self.allowed_localparts:
            return

        if not self.admin_writes_enabled:
            logger.info(
                f"Refusing broadcast change for {room_id} by {requester.user}: "
                f"admin writes are not enabled"
            )
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                "Changing broadcast rooms is not enabled",
                Codes.FORBIDDEN,
            )

        try:
            await PowerLevelsHelpers(self.api).verify(requester, room_id)
        except AuthError as e:
            logger.info(
                f"Refusing broadcast change for {room_id} by {requester.user}: {e.msg}"
            )
            raise
