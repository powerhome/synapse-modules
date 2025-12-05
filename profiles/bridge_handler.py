"""Profiles handler."""

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import HttpResponseException
from synapse.types import JsonDict

from .store import ProfileStore

if TYPE_CHECKING:
    from synapse.server import HomeServer


class BridgeHandler:
    """A handler for bridges via the Connect v2 bridge."""

    def __init__(
        self,
        store: ProfileStore,
        hs: "HomeServer",
        token: str,
        bridge_base_url: str,
    ):
        self.store = store
        self.hs = hs
        self.token = token
        self.bridge_base_url = bridge_base_url

    async def handle_all(self, requester) -> Tuple[int, JsonDict]:
        profiles = await self.store.get_profiles()
        return HTTPStatus.OK, profiles

    async def handle_listed(self, requester) -> Tuple[int, JsonDict]:
        profiles = await self.store.get_listed_profiles()
        return HTTPStatus.OK, profiles

    async def handle_get(self, requester, user_id: str) -> Tuple[int, JsonDict]:
        internal_profile = await self.store.get_profile(user_id)

        uri = f"{self.bridge_base_url}/_nitro/user_profile/{user_id}"
        args = {"user_id": requester.user.to_string()}
        headers = {b"Authorization": [b"Bearer " + self.token]}

        try:
            body = await self.hs.get_simple_http_client().get_json(
                uri, args=args, headers=headers
            )
            internal_profile.update(body)
        except HttpResponseException as e:
            logging.error(f"Failed to fetch profile for {user_id}: {e}")

        return HTTPStatus.OK, internal_profile
