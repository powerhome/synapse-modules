"""Power Levels"""

from copy import deepcopy

from synapse.api.errors import AuthError, SynapseError
from synapse.events import EventBase
from synapse.module_api import ModuleApi
from synapse.types import Requester

from ..people_conversations.store import PeopleConversationStore
from ..rules.room_helpers import is_user_in_room


class PowerLevelsHelpers:
    """Helper for room-level power level operations."""

    def __init__(self, module_api: ModuleApi) -> None:
        self.module_api = module_api
        self.hs = module_api._hs
        self.auth = module_api._hs.get_auth()
        self.db_pool = module_api._hs.get_datastores().main.db_pool

    async def get(self, room_id: str) -> EventBase:
        """
        Gets the power levels of the room.

        Args:
            room_id (str): The room_id to get the power levels of.

        Returns:
            EventBase: The power levels of the room.

        Raises:
            AuthError: If the power levels event is not found in the room state.
        """
        event_filter = [
            ("m.room.power_levels", None),
        ]

        room_state = await self.module_api.get_room_state(room_id, event_filter)
        try:
            power_levels = room_state[("m.room.power_levels", "")]
        except KeyError:
            raise AuthError(400, "Power levels event not found")

        return power_levels

    async def authorize(self, user_id: str, power_levels: EventBase, level=100) -> None:
        """
        Authorizes the request.

        Args:
            user_id (str): The id of the user making the request.
            power_levels (EventBase): The power levels of the room.
            level (int): The minimum power level required to make the request.

        Raises:
            AuthError: If the request is not authorized.
        """
        try:
            users = power_levels.content["users"]
            requester_power_level = users[user_id]
        except KeyError:
            raise AuthError(403, "User does not have permission to perform this action")

        if not isinstance(requester_power_level, int):
            raise AuthError(403, "User does not have permission to perform this action")

        if requester_power_level < level:
            raise AuthError(403, "User does not have permission to perform this action")

    async def grant_power_level_to_users(
        self,
        sender: str,
        recipients: list[str],
        room_id: str,
        power_levels: EventBase,
        level: int = 100,
    ) -> None:
        try:
            content = deepcopy(power_levels.content)
            necessary_to_create_event = False

            for recipient in recipients:
                if content["users"].get(recipient, 0) >= level:
                    continue
                content["users"][recipient] = level
                necessary_to_create_event = True

            current_power_levels = power_levels.content
            if content == current_power_levels:
                necessary_to_create_event = False

            if not necessary_to_create_event:
                return

            event_dict = {
                "type": "m.room.power_levels",
                "content": content,
                "room_id": room_id,
                "sender": sender,
                "state_key": "",
            }
            await self.module_api.create_and_send_event_into_room(event_dict)
        except Exception as e:
            raise SynapseError(400, f"Failed to ensure user has power level {e}")

    async def verify(self, requester: Requester, room_id: str) -> None:
        user_id = requester.user.to_string()

        if not await is_user_in_room(self.hs, user_id, room_id):
            raise AuthError(403, "User is not a member of this room")

        if await PeopleConversationStore(self.db_pool).is_people_conversation(room_id):
            raise AuthError(
                403, "Modifying DM or group conversation memberships is not supported"
            )

        power_levels = await self.get(room_id)
        await self.authorize(user_id, power_levels)
