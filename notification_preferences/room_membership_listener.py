"""Module for RoomMembershipListener class."""

import logging
from typing import Any

from synapse.api.constants import Membership
from synapse.module_api import EventBase, ModuleApi
from synapse.types import JsonDict, StateMap, UserID

from ..monkey_patches import after_create_room_callbacks
from ..people_conversations import Module as PeopleConversationsModule
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

        people_conversations_registered = any(
            getattr(cb, "__func__", None) is PeopleConversationsModule.after_create_room
            for cb in after_create_room_callbacks
        )
        if not people_conversations_registered:
            raise RuntimeError(
                "PeopleConversations module must register its after_create_room "
                "callback before RoomMembershipListener. "
                "Check module ordering in homeserver.yaml."
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
            member_count = await self.people_conversation_store.get_member_count(
                room_id
            )
            if member_count == 2:
                logger.info(
                    f"Not setting notification preference for creator {user_id} in {room_id} because it is a DM"
                )
                return

            await self._set_notification_preference(
                user_id.to_string(), room_id, Level(Level.EVERY_MESSAGE)
            )
            return

        await self._set_notification_preference(
            user_id.to_string(), room_id, Level(Level.ALL_AND_ME)
        )

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
            member_count = await self.people_conversation_store.get_member_count(
                event.room_id
            )

            if member_count == 2:
                logger.info(
                    f"Not setting notification preference for member {event.sender} in {event.room_id} because it is a DM"
                )
                return

            await self._set_notification_preference(
                event.sender, event.room_id, Level(Level.EVERY_MESSAGE)
            )
            return

        await self._set_notification_preference(
            event.sender, event.room_id, Level(Level.ALL_AND_ME)
        )

    async def _set_notification_preference(
        self, user_id: str, room_id: str, level: Level
    ):
        existing_rules = await self.notification_preferences_store.get_by_room_id(
            room_id, user_id
        )
        if existing_rules:
            logger.info(
                f"Notification preferences already exist for {user_id} in {room_id}. Not setting default."
            )
            return

        push_rule_manager = PushRuleManager(
            self.api._hs,
            room_id=room_id,
            user_id=user_id,
            notification_preference_event_type=self.notification_preference_event_type,
        )
        await push_rule_manager.update_to_level(level)
