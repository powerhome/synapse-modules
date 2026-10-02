"""Broadcast rooms guard."""

from typing import TYPE_CHECKING

from synapse.api.constants import EventTypes
from synapse.events import EventBase

from .store import BroadcastRoomStore

if TYPE_CHECKING:
    from synapse.storage.databases.main import DataStore


class BroadcastRoomGuard:
    """A class that prevents non-broadcasters from sending messages to a broadcast room."""

    def __init__(self, main_store: "DataStore", store: BroadcastRoomStore):
        self.main_store = main_store
        self.store = store

    async def can_create_edit_delete_message(self, event: EventBase) -> bool:
        if await self._is_redacting_reaction(event):
            return True

        # - creating/editing a message is an m.room.message event
        # - deleting a message is an m.room.redaction event
        # - reacting to a message is an m.reaction event,
        #   which everyone can send to a broadcast room
        if event.type in {
            EventTypes.Message,
            EventTypes.Redaction,
            EventTypes.Encrypted,
        }:
            broadcasters = await self.store.get_broadcasters(event.room_id)
            if broadcasters is not None and event.sender not in broadcasters:
                return False

        return True

    async def _is_redacting_reaction(self, event: EventBase) -> bool:
        if event.type == EventTypes.Redaction and event.redacts:
            redacted_event = await self.main_store.get_event(
                event.redacts, allow_none=True
            )
            if redacted_event and redacted_event.type == EventTypes.Reaction:
                return True

        return False
