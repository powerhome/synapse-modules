"""Routes for people conversations."""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet, parse_strings_from_args
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict

from .store import PeopleConversationStore

if TYPE_CHECKING:
    from synapse.server import HomeServer


class PeopleConversationResource(JsonResource):
    def __init__(self, hs: "HomeServer", store: PeopleConversationStore):
        JsonResource.__init__(self, hs, canonical_json=False)
        self.register_servlets(self, hs, store)

    @staticmethod
    def register_servlets(
        resource: HttpServer, hs: "HomeServer", store: PeopleConversationStore
    ) -> None:
        PeopleConversationServlet(hs, store).register(resource)


class PeopleConversationServlet(RestServlet):
    PATTERNS = [re.compile("^/_connect/people_conversations$")]
    CATEGORY = "People conversation requests"

    def __init__(self, hs: "HomeServer", store: PeopleConversationStore):
        super().__init__()
        self.auth = hs.get_auth()
        self.store = store

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        await self.auth.get_user_by_req(request)

        members = parse_strings_from_args(request.args, "members", required=True)
        room_id = await self.store.get_by_members(members)
        return HTTPStatus.OK, {"room_id": room_id}
