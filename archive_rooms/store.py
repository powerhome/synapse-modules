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

    async def get_rooms_eligible_for_auto_archive(self, cutoff_ts_ms: int) -> list[str]:
        """Return IDs of non-direct rooms eligible for automatic archival.

        A room is eligible when all of the following are true:
        - Created before cutoff_ts_ms
        - Not a direct message room (is_direct != true in m.room.create content)
        - Not already archived (not in blocked_rooms)
        - No m.room.message events at or after cutoff_ts_ms

        Args:
            cutoff_ts_ms: Epoch milliseconds representing the inactivity threshold.

        Returns:
            List of room IDs eligible for archival.
        """

        def _query(txn):
            sql = """
                WITH recent_message_rooms AS (
                    SELECT DISTINCT room_id
                    FROM events
                    WHERE type = 'm.room.message'
                      AND NOT outlier
                      AND origin_server_ts >= %(cutoff_ts_ms)s
                ),
                rooms_created_before_cutoff AS (
                    SELECT cse.room_id
                    FROM current_state_events cse
                    INNER JOIN events e ON e.event_id = cse.event_id
                    WHERE cse.type = 'm.room.create'
                      AND cse.state_key = ''
                      AND e.origin_server_ts < %(cutoff_ts_ms)s
                )
                SELECT r.room_id
                FROM rooms_created_before_cutoff r
                WHERE r.room_id NOT IN (SELECT room_id FROM blocked_rooms)
                  AND r.room_id NOT IN (SELECT room_id FROM recent_message_rooms)
                  AND r.room_id NOT IN (
                      SELECT room_id FROM connect.people_conversations
                      WHERE room_id IS NOT NULL
                  )
            """  # noqa: S608
            txn.execute(sql, {"cutoff_ts_ms": cutoff_ts_ms})
            return [row[0] for row in txn]

        return await self.store.db_pool.runInteraction(
            "get_rooms_eligible_for_auto_archive", _query
        )
