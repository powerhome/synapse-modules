"""Module for AudiencesBatchMembershipProcessor class, applying individual Synapse membership changes based on changes to audiences."""

import logging
from typing import Any, Dict

from synapse.api.constants import Membership
from synapse.api.errors import (
    HttpResponseException,
    LimitExceededError,
    UnstableSpecAuthError,
)
from synapse.module_api import ModuleApi

from ..helpers.user import UserHelpers


class AudiencesBatchMembershipProcessor:
    """This class applies individual room membership changes in Synapse, based on changes to audiences.

    The Matrix spec (and Synapse API) has two fundamental limitations related to room membership changes:
    1) In at least some cases, members cannot add another user to a room (they must instead invite them).
    2) The Matrix API only allows a single membership change per HTTP request.

    These lead to slow performance in cases where many users need to be added to or removed from a room.

    To improve performance when batch membership changes are applied, this class applies membership changes via
    the Synapse module_api which is significantly faster than the Matrix API (via HTTP requests).

    Attributes:
        config (Dict[str, Any]): Configuration dictionary containing bot_user_ids, idp_id, and hs_token.
        api (ModuleApi): An instance of the Synapse ModuleApi used to interact with Synapse.
    """

    def __init__(self, config: Dict[str, Any], api: ModuleApi):
        self.bot_user_ids = config["bot_user_ids"]
        self._api = api

        self.idp_id = f"oidc-{config['idp_id']}"
        self.store = api._store
        self.hs_token = config["hs_token"]
        self.audiences_bot_user_id = config["audiences_bot_user_id"]

    async def process_batch_memberships(self, room_id: str):
        """
        Processes batches of membership changes.

        Args:
            room_id (str): The ID of the room.
        """
        desired_room_members = await self._desired_room_members(room_id)
        invited, joined = await self._fetch_invited_and_joined_users(room_id)

        logging.info(
            f"Room {room_id}: invited_members: {invited} ... joined_members: {joined} ... desired_room_members: {desired_room_members}"
        )

        room_members_to_add = [
            mxid for mxid in desired_room_members if mxid and mxid not in joined
        ]
        logging.info(f"Room {room_id}: Adding users {room_members_to_add}")
        await self._process_batch_memberships_type(
            room_id, "invite", room_members_to_add
        )

        room_members_to_remove = [
            mxid
            for mxid in (invited | joined)
            if mxid
            and mxid not in desired_room_members
            and mxid not in self.bot_user_ids
        ]
        logging.info(f"Room {room_id}: Removing users {room_members_to_remove}")
        await self._process_batch_memberships_type(
            room_id, "leave", room_members_to_remove
        )

    async def restore_memberships_from_audience_criteria(self, room_id: str) -> None:
        """
        Restores memberships in a room based on audience criteria data.

        Args:
            room_id (str): The ID of the room.
        """
        desired_room_members = await self._desired_room_members(room_id)

        logging.info(f"Room {room_id}: Restoring memberships to {desired_room_members}")
        await self._process_batch_memberships_type(
            room_id, "invite", list(desired_room_members)
        )

    async def _fetch_invited_and_joined_users(
        self, room_id: str
    ) -> tuple[set[str], set[str]]:
        user_memberships = await self.store.get_local_users_related_to_room(room_id)
        invited = {
            user_mxid
            for (user_mxid, membership) in user_memberships
            if membership == Membership.INVITE
        }
        joined = {
            user_mxid
            for (user_mxid, membership) in user_memberships
            if membership == Membership.JOIN
        }
        return invited, joined

    async def _process_batch_memberships_type(
        self, room_id: str, kind: str, mxids: list[str]
    ):
        for mxid in mxids:
            try:
                await self._api.update_room_membership(
                    sender=self.audiences_bot_user_id,
                    target=mxid,
                    room_id=room_id,
                    content=None,
                    new_membership=kind,
                )
            except UnstableSpecAuthError as e:
                if "already in the room" in str(e.msg):
                    pass
            except LimitExceededError as e:
                if e.retry_after_ms is not None:
                    # .. See https://github.com/matrix-org/synapse/issues/6286#issuecomment-646944920 and
                    # .. https://github.com/matrix-org/synapse/pull/9648
                    logging.warning(
                        f"[AudiencesBatchMembershipProcessor]: {e.msg}. Is {self.audiences_bot_user_id} in the ratelimit_override table?"
                    )
                    time_to_sleep = e.retry_after_ms / 1000
                    await self._api.sleep(time_to_sleep)
                    await self._api.update_room_membership(
                        sender=self.audiences_bot_user_id,
                        target=mxid,
                        room_id=room_id,
                        content=None,
                        new_membership=kind,
                    )

    async def _desired_room_members(self, room_id: str) -> set[str]:
        """
        Returns the desired room members for the provided event.

        Args:
            room_id (str): The room id.

        Returns:
            list[str]: The desired room members.
        """
        subs = await self._get_subs(room_id)

        results = await self.store.db_pool.simple_select_many_batch(
            "user_external_ids",
            column="external_id",
            iterable=subs,
            retcols={"user_id"},
            keyvalues={"auth_provider": self.idp_id},
        )
        # Omitting deactivated users from desired will allow Audiences to
        # kick deactivated users but not add them
        deactivated_users = await UserHelpers.get_deactivated_users(self.store.db_pool)
        return {mxid for (mxid,) in results if mxid not in deactivated_users}

    async def _get_subs(self, room_id: str) -> list[str]:
        path = "http://audiences:3000/audiences/api/subs"

        headers = {b"Authorization": [b"Bearer " + self.hs_token.encode("ascii")]}

        try:
            response = await self._api.http_client.get_json(
                uri=path,
                headers=headers,
                args={"room_mxid": room_id},
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        assert response["room_mxid"] == room_id
        return response["subs"]
