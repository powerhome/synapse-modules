"""Engine to create self-conversation rooms"""

import logging
from typing import Optional

from synapse.module_api import ModuleApi, ProfileInfo

from connect.base_config import BaseConfig
from connect.custom_types import CUSTOM_EVENT_TYPE


class Config(BaseConfig):
    """Configuration for self-conversation creator."""

    unbridged_room_event_type: CUSTOM_EVENT_TYPE


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

        self.api.register_account_validity_callbacks(on_user_login=self.on_user_login)
        self.api.register_third_party_rules_callbacks(
            on_profile_update=self.on_profile_update
        )

    async def on_user_login(
        self, user_id: str, auth_provider_type: str, auth_provider_id: str
    ) -> None:
        localpart = user_id.split(":")[0][1:]

        await self._get_self_dm_id(user_id) or await self._create_self_dm(
            user_id, localpart
        )

    async def on_profile_update(
        self,
        user_id: str,
        new_profile: ProfileInfo,
        by_admin: bool,
        deactivation: bool,
    ) -> None:
        room_id = await self._get_self_dm_id(user_id)
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

    async def _get_self_dm_id(self, user_id: str) -> Optional[str]:
        return await get_self_dm_id(self.api, user_id)

    async def _create_self_dm(self, user_id: str, localpart: str) -> str:
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

        new_account_data = await self._get_new_account_data(user_id, room_id)

        await self.api.account_data_manager.put_global(
            user_id, "m.direct", new_account_data
        )

        return room_id

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
