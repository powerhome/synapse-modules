"""Module for join room alias servlet."""

from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.http.site import SynapseRequest
from synapse.rest.client.room import JoinRoomAliasServlet
from synapse.types import JsonDict

from ..people_conversations.store import PeopleConversationStore
from .audiences_membership_handler import AudiencesMembershipHandler

if TYPE_CHECKING:
    from synapse.server import HomeServer


class ConnectJoinRoomAliasServlet(JoinRoomAliasServlet):
    """A servlet that overrides what happens when a user joins a public room from the room browser.

    E.g., POST /_matrix/client/r0/join/!abc123:localhost
    """

    def __init__(self, hs: "HomeServer", config: dict):
        super().__init__(hs)
        self.hs = hs
        self.bots = config.get("bot_user_ids", [])
        self.hs_token = config["hs_token"]
        self.idp_id = f"oidc-{config['idp_id']}"

    async def on_POST(
        self, request: SynapseRequest, room_identifier: str
    ) -> Tuple[int, JsonDict]:
        is_main_process = self.hs.config.worker.worker_name is None
        assert is_main_process

        requester = await self.auth.get_user_by_req(request)

        if requester.user.to_string() in self.bots:
            # We cannot simply call super().on_POST because that method also calls get_user_by_req.
            #
            # Synapse prevents calling get_user_by_req more than once for a given request, failing with the following error:
            #
            # Traceback (most recent call last):
            # .
            # .
            # .
            # File "/usr/local/lib/python3.11/site-packages/synapse/api/auth/internal.py", line 160, in _wrapped_get_user_by_req
            #     request.requester = requester
            #     ^^^^^^^^^^^^^^^^^
            # File "/usr/local/lib/python3.11/site-packages/synapse/http/site.py", line 159, in requester
            #     assert self._requester is None
            #         ^^^^^^^^^^^^^^^^^^^^^^^
            # AssertionError
            #
            # See https://github.com/matrix-org/synapse/blob/develop/synapse/http/site.py#L158-L159 for the code.
            #
            # Since super().on_POST just calls self._do after getting the requester, we call self._do directly instead.
            #
            return await self._do(request, requester, room_identifier, None)

        room_id, _ = await self.resolve_room_id(room_identifier)
        db_pool = self.hs.get_datastores().main.db_pool
        if not await PeopleConversationStore(db_pool).is_people_conversation(room_id):
            audiences = AudiencesMembershipHandler(self.hs, self.hs_token, self.idp_id)
            await audiences.update(room_id, [requester.user], "invite")

        return HTTPStatus.OK, {"room_id": room_id}
