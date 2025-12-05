"""Module for RoomMembershipListener class."""

import logging
from typing import Any

from synapse.api.constants import Membership
from synapse.module_api import EventBase, ModuleApi
from synapse.types import JsonDict, StateMap, UserID

from ..monkey_patches import after_create_room_callbacks
from ..people_conversations.store import PeopleConversationStore
from .notification_preferences import Level
from .push_rule_manager import PushRuleManager
from .store import NotificationPreferenceStore

logger = logging.getLogger(__name__)


class RoomMembershipListener:
    """Listens for m.room.member events, and applies notification preference related changes."""

    def __init__(self, api: ModuleApi, config: dict[str, Any]):
        self.api = api
        self.hs = api._hs
        self.bot_user_ids = config["bot_user_ids"]
        self.room_fully_created_event_type = config["room_fully_created_event_type"]
        self.notification_preference_event_type = config[
            "notification_preference_event_type"
        ]
        main_store = self.api._hs.get_datastores().main
        self.people_conversation_store = PeopleConversationStore(main_store.db_pool)
        self.notification_preferences_store = NotificationPreferenceStore(
            main_store.db_pool,
            self.notification_preference_event_type,
        )

        after_create_room_callbacks.append(self._after_create_room)

    def register_third_party_rules_callbacks(self) -> None:
        self.api.register_third_party_rules_callbacks(
            on_new_event=self._on_new_event,
        )

    # handle room creator joining new room
    async def _after_create_room(self, user_id: UserID, room_id: str, config: JsonDict):
        if user_id.to_string() in self.bot_user_ids:
            return

        if config.get("is_direct") is True:
            logger.info(
                f"Not setting default preference for creator {user_id} because {room_id} is a people conversation"
            )
            return

        await self._set_default_notification_preference(user_id.to_string(), room_id)

    # handle invitees joining new room
    async def _on_new_event(
        self, event: "EventBase", state_events: StateMap["EventBase"]
    ):
        if event.sender in self.bot_user_ids:
            return

        if event.type != "m.room.member":
            return

        if event.membership != Membership.JOIN:
            return

        created_event = state_events.get((self.room_fully_created_event_type, ""))
        if not created_event:
            return

        if created_event.content.get("is_direct") is True:
            logger.info(
                f"Not setting default preference for invitee {event.sender} because {event.room_id} is a people conversation"
            )
            return

        await self._set_default_notification_preference(event.sender, event.room_id)

    async def _set_default_notification_preference(self, user_id: str, room_id: str):
        existing_rules = await self.notification_preferences_store.get_by_room_id(
            room_id, user_id
        )
        if existing_rules:
            logger.info(
                f"Notification preferences already exist for {user_id} in {room_id}. Not setting default."
            )
            return

        default_level = Level(Level.ALL_AND_ME)
        push_rule_manager = PushRuleManager(
            self.api._hs,
            room_id=room_id,
            user_id=user_id,
            notification_preference_event_type=self.notification_preference_event_type,
        )
        await push_rule_manager.update_to_level(default_level)
