"""Recent Conversations API."""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.http.server import JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict

from .store import RecentConversationsStore

if TYPE_CHECKING:
    from synapse.server import HomeServer


class RecentConversationsResource(JsonResource):
    """A resource for managing recent conversations."""

    def __init__(self, hs: "HomeServer"):
        JsonResource.__init__(self, hs, canonical_json=False)
        RecentConversationsServlet(hs).register(self)


class RecentConversationsServlet(RestServlet):
    """A servlet for managing recent conversations."""

    PATTERNS = [re.compile("^/_connect/recent_conversations$")]
    CATEGORY = "Recent conversations requests"

    def __init__(self, hs: "HomeServer"):
        super().__init__()
        self.store = hs.get_datastores().main
        self.auth = hs.get_auth()

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        """Handle GET request to fetch the latest event for rooms that the user is currently a member of.

        Args:
            request: The HTTP request object.

        Returns:
            A tuple containing the HTTP status code and a JSON response with room data.

            The response keys are the rooms the user is a member of (including rooms and people convos).
            The values are the latest "renderable event" in that room.
            A renderable event, in this context, is one of these types: ['m.room.message', 'm.room.create', 'm.room.join_rules'].

            The other data in the response is just enough event data that the client needs to sort conversations by recency.
            Important: the response does not include the full event, just the necessary fields to identify it.
        """
        requester = await self.auth.get_user_by_req(request)
        user_id = requester.user.to_string()
        room_ids = await self.store.get_rooms_for_user(user_id)
        if not room_ids:
            return HTTPStatus.OK, {}

        room_ids_list = list(room_ids)
        recent_conversations_store = RecentConversationsStore(self.store.db_pool)
        latest_events = await recent_conversations_store.fetch_latest_events_for_rooms(
            room_ids_list
        )

        return HTTPStatus.OK, latest_events.to_dict()
