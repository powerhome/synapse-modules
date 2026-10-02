"""People conversations module."""

import logging
from http import HTTPStatus

from synapse.api.errors import Codes, StoreError
from synapse.module_api import ModuleApi
from synapse.types import JsonDict, UserID

from ..helpers.user import UserHelpers
from ..monkey_patches import (
    after_create_room_callbacks,
    before_create_room_callbacks,
    on_create_room_errbacks,
)
from .account_data_handler import ConnectAccountDataHandler
from .api import PeopleConversationResource
from .store import PeopleConversationStore
from .tools import AccountDataRepairer

logger = logging.getLogger(__name__)


class Module:
    """A module that handles DMs and group conversations."""

    def __init__(self, config: dict, api: ModuleApi):
        hs = api._hs
        main_store = hs.get_datastores().main

        self.db_pool = main_store.db_pool
        self.store = PeopleConversationStore(main_store.db_pool)
        self.account_data_handler = ConnectAccountDataHandler(hs)

        api.register_web_resource(
            path="/_connect/people_conversations",
            resource=PeopleConversationResource(hs, self.store),
        )

        before_create_room_callbacks.append(self.before_create_room)
        after_create_room_callbacks.append(self.after_create_room)
        on_create_room_errbacks.append(self.on_create_room_error)

        AccountDataRepairer(api)

    async def before_create_room(self, user_id: UserID, config: JsonDict):
        if config.get("is_direct") is not True:
            return

        invitees = config.get("invite")
        if not invitees:
            return

        # Do not allow request with deactivated users
        deactivated_users = await UserHelpers.get_deactivated_users(self.db_pool)
        if set(invitees) & deactivated_users:
            raise StoreError(
                HTTPStatus.FORBIDDEN,
                "people conversations may not contain deactivated users",
                Codes.FORBIDDEN,
            )

        creator = user_id.to_string()
        first_invitee = invitees[0]
        members = [creator] + invitees
        await self.store.store_draft_people_conversation(
            creator, first_invitee, members
        )

    async def on_create_room_error(self, user_id: UserID, config: JsonDict):
        if config.get("is_direct") is not True:
            return

        invitees = config.get("invite")
        if not invitees:
            return

        creator = user_id.to_string()
        members = [creator] + invitees
        await self.store.delete_draft_people_conversation(members)

    async def after_create_room(self, user_id: UserID, room_id: str, config: JsonDict):
        if config.get("is_direct") is not True:
            return

        invitees = config.get("invite")
        if not invitees:
            return

        creator = user_id.to_string()
        first_invitee = invitees[0]
        members = [creator] + invitees
        await self.store.set_people_conversation_id(members, room_id)
        await self._mark_room_as_direct_message(creator, first_invitee, room_id)

    async def _mark_room_as_direct_message(
        self, user_id: str, dm_user_id: str, room_id: str
    ):
        await self.account_data_handler.append_m_direct_account_data_for_user(
            user_id, dm_user_id, room_id
        )
