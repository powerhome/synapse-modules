"""Notify Audiences about room membership changes."""

import logging
from typing import TYPE_CHECKING

from synapse.api.errors import HttpResponseException
from synapse.types import JsonDict, UserID

from ..archive_rooms.store import ArchiveRoomStore
from ..helpers.request_context import cpg_headers, init_request_context, log_event
from ..people_conversations.store import PeopleConversationStore

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class AudiencesMembershipHandler:
    """Notifies audiences about direct room membership changes."""

    def __init__(self, hs: "HomeServer", hs_token: str, idp_id: str):
        self.hs = hs
        self.hs_token = hs_token
        self.idp_id = idp_id
        self.archive_room_store = ArchiveRoomStore(hs.get_datastores().main)

    async def update(
        self,
        room_id: str,
        users: list[UserID],
        membership_action: str,
        request_id: str | None = None,
    ) -> JsonDict:
        """
        Updates memberships in a room.

        Args:
            room_id (str): The ID of the room.
            users (list[UserID]): The user mxids to update (e.g. ["@first_name.last_name:example.com"]).
            membership_action (str): The membership action to perform.
            request_id (str): Optional CPG request ID for tracing; a fresh ID is generated when absent.

        Returns:
            JsonDict: The response body from the membership update request, or an empty dict if there are no users.

        Raises:
            to_synapse_error: If an HTTP error occurs
        """
        init_request_context(request_id=request_id, action="membership_update")

        if await self.archive_room_store.is_archived(room_id):
            log_event(
                logger,
                "Not updating memberships because room is archived",
                room_id=room_id,
                action=membership_action,
            )
            return {}

        db_pool = self.hs.get_datastores().main.db_pool
        if await PeopleConversationStore(db_pool).is_people_conversation(room_id):
            log_event(
                logger,
                "Not updating memberships; people conversations are unsupported",
                level=logging.WARNING,
                room_id=room_id,
                action=membership_action,
            )
            return {}

        user_subs = await self._user_subs(users)
        if not user_subs:
            return {}

        memberships_to_update = {
            "room_mxid": room_id,
            "user_subs": user_subs,
            "membership_action": membership_action,
        }

        log_event(
            logger,
            "Notifying CPG of membership change",
            room_id=room_id,
            action=membership_action,
            user_count=len(user_subs),
            endpoint="/audiences/api/extra_users",
        )

        try:
            path = "http://audiences:3000/audiences/api/extra_users"
            headers = {
                b"Authorization": [b"Bearer " + self.hs_token.encode("ascii")],
                **cpg_headers(),
            }
            body = await self.hs.get_simple_http_client().put_json(
                uri=path,
                json_body=memberships_to_update,
                headers=headers,
            )
        except HttpResponseException as e:
            log_event(
                logger,
                "Failed to notify CPG of membership change",
                level=logging.ERROR,
                room_id=room_id,
                action=membership_action,
                status=e.code,
            )
            raise e.to_synapse_error() from e

        log_event(
            logger,
            "CPG notified of membership change",
            room_id=room_id,
            action=membership_action,
            user_count=len(user_subs),
        )
        return body

    async def _user_subs(self, users: list[UserID]) -> list[str]:
        if not users:
            return []

        user_ids = {user.to_string() for user in users}
        store = self.hs.get_datastores().main
        results = await store.db_pool.simple_select_many_batch(
            "user_external_ids",
            column="user_id",
            iterable=user_ids,
            retcols={"external_id"},
            keyvalues={"auth_provider": self.idp_id},
        )

        subs = {sub for (sub,) in results}
        return list(subs)
