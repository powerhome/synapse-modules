"""Profiles handler."""

from http import HTTPStatus
from typing import Tuple

from synapse.types import JsonDict

from .store import ProfileStore


class ProfileHandler:
    """A handler for profiles."""

    def __init__(self, store: ProfileStore):
        self.store = store

    async def handle_all(self, requester) -> Tuple[int, JsonDict]:
        profiles = await self.store.get_profiles()
        return HTTPStatus.OK, profiles

    async def handle_listed(self, requester) -> Tuple[int, JsonDict]:
        profiles = await self.store.get_listed_profiles()
        return HTTPStatus.OK, profiles

    async def handle_get(self, requester, user_id: str) -> Tuple[int, JsonDict]:
        profile = await self.store.get_profile(user_id)
        return HTTPStatus.OK, profile
