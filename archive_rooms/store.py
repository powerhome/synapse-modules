"""Archive rooms store."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synapse.storage.databases.main import DataStore


# Selects every room id that is currently registered as a user's self-DM.
#
# A self-DM is a room listed under the user's *own* mxid key in their m.direct
# account data (the peer is the user themselves), which is how
# connect.self_dm.SelfConversationCreator records the room. account_data.content
# is a jsonb object of {peer_mxid: [room_id, ...]}; the self-DM entry is the one
# keyed by account_data.user_id.
#
# jsonb_exists() is used instead of the `?` operator because Synapse's database
# layer rewrites every `?` into a `%s` placeholder (see
# DatabaseEngine.convert_param_style), which would corrupt the operator.
_SELF_DM_ROOMS_SQL = """
    SELECT DISTINCT room_id
    FROM account_data ad
    CROSS JOIN LATERAL jsonb_array_elements_text(
        ad.content::jsonb -> ad.user_id
    ) AS room_id
    WHERE ad.account_data_type = 'm.direct'
      AND jsonb_exists(ad.content::jsonb, ad.user_id)
"""


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

    async def is_self_dm(self, room_id: str) -> bool:
        """Check whether a room is a user's self-DM (Notes) room.

        A self-DM is a room listed under the owner's own mxid key in their
        m.direct account data. These rooms must never be archived: archival
        parts and forgets the only member, leaving an empty room that cannot be
        rejoined, so the user permanently loses the ability to message
        themselves.

        Args:
            room_id: The room ID to check.

        Returns:
            bool: True if the room is registered as someone's self-DM.
        """

        def _query(txn):
            sql = """
                SELECT EXISTS (
                    SELECT 1
                    FROM account_data ad
                    WHERE ad.account_data_type = 'm.direct'
                      AND jsonb_exists(
                          ad.content::jsonb -> ad.user_id, %(room_id)s
                      )
                )
            """
            txn.execute(sql, {"room_id": room_id})
            row = txn.fetchone()
            return bool(row[0]) if row else False

        return await self.store.db_pool.runInteraction(
            "archive_rooms_is_self_dm", _query
        )

    async def get_rooms_eligible_for_auto_archive(self, cutoff_ts_ms: int) -> list[str]:
        """Return IDs of rooms eligible for automatic archival.

        A room is eligible when all of the following are true:
        - Created before cutoff_ts_ms
        - Not already archived (not in blocked_rooms)
        - No m.room.message events at or after cutoff_ts_ms
        - Not a self-DM (not registered under a user's own m.direct key)
        - Not a people-conversation (not in connect.people_conversations)

        Args:
            cutoff_ts_ms: Epoch milliseconds representing the inactivity threshold.

        Returns:
            List of room IDs eligible for archival.
        """

        def _query(txn):
            sql = f"""
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
                ),
                self_dm_rooms AS (
                    {_SELF_DM_ROOMS_SQL}
                )
                SELECT r.room_id
                FROM rooms_created_before_cutoff r
                WHERE r.room_id NOT IN (SELECT room_id FROM blocked_rooms)
                  AND r.room_id NOT IN (SELECT room_id FROM recent_message_rooms)
                  AND r.room_id NOT IN (SELECT room_id FROM self_dm_rooms)
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
