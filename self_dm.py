"""Engine to create self-conversation rooms"""

import logging
from typing import Optional

from synapse.api.constants import Membership
from synapse.module_api import ModuleApi, ProfileInfo
from synapse.util.async_helpers import Linearizer

from connect.base_config import BaseConfig
from connect.custom_types import CUSTOM_EVENT_TYPE
from connect.helpers.user import UserHelpers


class Config(BaseConfig):
    """Configuration for self-conversation creator."""

    unbridged_room_event_type: CUSTOM_EVENT_TYPE
    # When enabled, on_user_login recreates a self-DM the user is no longer
    # joined to (archived, left, or purged) instead of leaving them pointed at a
    # dead room. Defaults off so behavior is unchanged until a tenant opts in.
    healing_enabled: bool = False


logger = logging.getLogger(__name__)


async def get_self_dm_id(api: ModuleApi, user_id: str) -> Optional[str]:
    account_data = await api.account_data_manager.get_global(user_id, "m.direct")

    if not account_data:
        return None

    self_dm_ids = account_data.get(user_id)
    if not self_dm_ids:
        return None
    else:
        return self_dm_ids[0]


class SelfConversationCreator:
    """Class to handle self-conversation engine"""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        self.api = api
        self.unbridged_room_event_type = config["unbridged_room_event_type"]
        self.healing_enabled = config.get("healing_enabled", False)
        self.main_store = api._hs.get_datastores().main
        # Per-process only: serialises heal/create for the same user on this worker
        # (e.g. two devices hitting the same auth worker). Logins are spread across
        # auth-worker replicas, so two simultaneous logins can still run on different
        # workers and both create a room; the last m.direct write wins and the other
        # room is an empty orphan. Low impact; not worth a distributed lock.
        self._heal_linearizer = Linearizer(name="self_dm_heal_linearizer")

        self.api.register_account_validity_callbacks(on_user_login=self.on_user_login)
        self.api.register_third_party_rules_callbacks(
            on_profile_update=self.on_profile_update
        )

    async def on_user_login(
        self, user_id: str, auth_provider_type: str, auth_provider_id: str
    ) -> None:
        localpart = user_id.split(":")[0][1:]

        if not self.healing_enabled:
            await self._get_self_dm_id(user_id) or await self._create_self_dm(
                user_id, localpart
            )
            return

        if await UserHelpers.is_user_deactivated(self.main_store, user_id):
            return

        await self._heal_or_create_self_dm(user_id, localpart)

    async def on_profile_update(
        self,
        user_id: str,
        new_profile: ProfileInfo,
        by_admin: bool,
        deactivation: bool,
    ) -> None:
        room_id = await self._resolve_self_dm_for_rename(user_id)
        if room_id:
            await self.api.create_and_send_event_into_room(
                {
                    "type": "m.room.name",
                    "room_id": room_id,
                    "sender": user_id,
                    "state_key": "",
                    "content": {
                        "name": f"{new_profile.display_name} (You)",
                    },
                }
            )

    async def _resolve_self_dm_for_rename(self, user_id: str) -> Optional[str]:
        # Return the self-DM to rename on profile update. With healing on, only
        # a live (joined) self-DM is renamed; sending m.room.name into a dead
        # room would fail because the user is not a member. With healing off,
        # behavior is unchanged.
        if not self.healing_enabled:
            return await self._get_self_dm_id(user_id)

        room_id = await self._get_live_self_dm_id(user_id)
        if not room_id:
            logger.info(f"Skipping self-DM rename for {user_id}: no live self-DM found")
        return room_id

    async def _get_self_dm_id(self, user_id: str) -> Optional[str]:
        return await get_self_dm_id(self.api, user_id)

    async def _get_self_dm_ids(self, user_id: str) -> list[str]:
        account_data = await self.api.account_data_manager.get_global(
            user_id, "m.direct"
        )
        if not account_data:
            return []
        return list(account_data.get(user_id) or [])

    async def _is_self_dm_alive(self, user_id: str, room_id: str) -> bool:
        # A self-DM is alive only if the user's current membership is join.
        # None membership means the room was purged; leave means it was archived
        # or the user left. Both make the room unusable as a self-DM.
        (
            membership,
            _,
        ) = await self.main_store.get_local_current_membership_for_user_in_room(
            user_id, room_id
        )
        return membership == Membership.JOIN

    async def _get_live_self_dm_id(self, user_id: str) -> Optional[str]:
        for room_id in await self._get_self_dm_ids(user_id):
            if await self._is_self_dm_alive(user_id, room_id):
                return room_id
        return None

    async def _heal_or_create_self_dm(self, user_id: str, localpart: str) -> str:
        async with self._heal_linearizer.queue(user_id):
            self_dm_ids = await self._get_self_dm_ids(user_id)

            for room_id in self_dm_ids:
                if await self._is_self_dm_alive(user_id, room_id):
                    return room_id

            if not self_dm_ids:
                # First-time creation: no self-DM recorded yet.
                return await self._create_self_dm(user_id, localpart)

            # Every recorded self-DM is dead. Create a fresh one and repoint
            # m.direct at it, dropping the dead ids.
            return await self._heal_self_dm(user_id, localpart, self_dm_ids)

    async def _create_self_dm_room(self, user_id: str, localpart: str) -> str:
        profile = await self.api.get_profile_for_user(localpart)

        config = {
            "name": f"{profile.display_name} (You)",
            "topic": (
                "This is a space to jot down notes, list to-dos, or keep links and "
                "files handy. You can also talk to yourself here, but please "
                "bear in mind you'll have to supply both sides of the conversation."
            ),
            "invite": [],
            "is_direct": True,
            "preset": "trusted_private_chat",
            "creation_content": {"type": self.unbridged_room_event_type},
        }

        room_id, _ = await self.api.create_room(user_id, config)
        logger.info(f"Created self DM {room_id} for {user_id}")
        return room_id

    async def _create_self_dm(self, user_id: str, localpart: str) -> str:
        room_id = await self._create_self_dm_room(user_id, localpart)

        new_account_data = await self._get_new_account_data(user_id, room_id)

        await self.api.account_data_manager.put_global(
            user_id, "m.direct", new_account_data
        )

        return room_id

    async def _heal_self_dm(
        self, user_id: str, localpart: str, dead_ids: list[str]
    ) -> str:
        # Create the room first; if it fails, m.direct is left untouched.
        new_room_id = await self._create_self_dm_room(user_id, localpart)

        account_data = (
            await self.api.account_data_manager.get_global(user_id, "m.direct") or {}
        )
        new_account_data = dict(account_data)
        # Every id under the self key was dead, so replace the whole list with
        # the new room (first, where get_self_dm_id reads it). Other peer keys
        # (real DMs) are preserved by the dict copy.
        new_account_data[user_id] = [new_room_id]

        await self.api.account_data_manager.put_global(
            user_id, "m.direct", new_account_data
        )

        logger.info(
            f"Healed self DM for {user_id}: replaced {dead_ids} with {new_room_id}"
        )

        return new_room_id

    async def _get_new_account_data(self, user_id: str, self_dm_id: str):
        account_data = await self.api.account_data_manager.get_global(
            user_id, "m.direct"
        )

        if not account_data:
            return {user_id: [self_dm_id]}

        new_account_data = dict(account_data)
        self_dm_ids = list(new_account_data.get(user_id, []))

        if self_dm_id not in self_dm_ids:
            self_dm_ids.append(self_dm_id)
            new_account_data[user_id] = self_dm_ids

        return new_account_data
