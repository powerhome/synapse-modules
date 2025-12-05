"""Notify Audiences about room membership changes."""

import logging
from typing import TYPE_CHECKING

from synapse.api.errors import HttpResponseException
from synapse.types import JsonDict, UserID

from ..archive_rooms.store import ArchiveRoomStore
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
        self, room_id: str, users: list[UserID], membership_action: str
    ) -> JsonDict:
        """
        Updates memberships in a room.

        Args:
            room_id (str): The ID of the room.
            users (list[UserID]): The user mxids to update (e.g. ["@first_name.last_name:example.com"]).
            membership_action (str): The membership action to perform.

        Returns:
            JsonDict: The response body from the membership update request, or an empty dict if there are no users.

        Raises:
            to_synapse_error: If an HTTP error occurs
        """
        if await self.archive_room_store.is_archived(room_id):
            logger.info(
                f"Room {room_id}: not updating memberships because it is archived"
            )
            return {}

        db_pool = self.hs.get_datastores().main.db_pool
        if await PeopleConversationStore(db_pool).is_people_conversation(room_id):
            logger.warning(
                f"Room {room_id}: not updating memberships because people convos are unsupported."
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

        logger.info(f"AudiencesMembershipHandler.update - {memberships_to_update}")

        try:
            path = "http://audiences:3000/audiences/api/extra_users"
            headers = {b"Authorization": [b"Bearer " + self.hs_token.encode("ascii")]}
            body = await self.hs.get_simple_http_client().put_json(
                uri=path,
                json_body=memberships_to_update,
                headers=headers,
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

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
