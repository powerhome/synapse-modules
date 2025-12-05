"""Routes for profiles."""

import re
from typing import TYPE_CHECKING, Tuple

from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict

from .handler import ProfileHandler

if TYPE_CHECKING:
    from synapse.server import HomeServer


class ProfileResource(JsonResource):
    def __init__(self, hs: "HomeServer", handler: ProfileHandler):
        JsonResource.__init__(self, hs, canonical_json=False)
        self.register_servlets(self, hs, handler)

    @staticmethod
    def register_servlets(
        resource: HttpServer, hs: "HomeServer", handler: ProfileHandler
    ) -> None:
        DirectoryServlet(hs, handler).register(resource)
        ProfileListServlet(hs, handler).register(resource)
        ProfileServlet(hs, handler).register(resource)


class DirectoryServlet(RestServlet):
    PATTERNS = [re.compile("^/_connect/directory$")]
    CATEGORY = "Profile requests"

    def __init__(self, hs: "HomeServer", handler: ProfileHandler):
        super().__init__()
        self.auth = hs.get_auth()
        self.handler = handler

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)

        return await self.handler.handle_listed(requester)


class ProfileListServlet(RestServlet):
    PATTERNS = [re.compile("^/_connect/profiles$")]
    CATEGORY = "Profile requests"

    def __init__(self, hs: "HomeServer", handler: ProfileHandler):
        super().__init__()
        self.auth = hs.get_auth()
        self.handler = handler

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)

        return await self.handler.handle_all(requester)


class ProfileServlet(RestServlet):
    PATTERNS = [re.compile("^/_connect/profiles/(?P<user_id>[^/]*)")]
    CATEGORY = "Profile requests"

    def __init__(self, hs: "HomeServer", handler: ProfileHandler):
        super().__init__()
        self.auth = hs.get_auth()
        self.handler = handler

    async def on_GET(
        self, request: SynapseRequest, user_id: str
    ) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)

        return await self.handler.handle_get(requester, user_id)
