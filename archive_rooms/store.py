"""Archive rooms store."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synapse.storage.databases.main import DataStore


class ArchiveRoomStore:
    """A store for archive room operations."""

    def __init__(self, store: "DataStore"):
        self.store = store

    async def is_archived(self, room_id: str) -> bool:
        """Check if a room is archived and return a boolean result.

        Args:
            room_id: The room ID to check

        Returns:
            bool: True if the room is archived, False otherwise.
        """
        # A room being blocked is our source of truth for archival
        room_blocked = await self.store.is_room_blocked(room_id)
        return bool(room_blocked)
