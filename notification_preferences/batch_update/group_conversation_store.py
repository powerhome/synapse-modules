"""Store for group conversation batch update queries."""
from __future__ import annotations

from typing import NamedTuple

from synapse.storage.database import DatabasePool, LoggingTransaction


class RoomMember(NamedTuple):
    """A room member identified by room_id and user_name."""

    room_id: str
    user_name: str


class GroupConversationStore:
    """Database queries for group conversation batch operations."""

    def __init__(self, db_pool: DatabasePool):
        self.db_pool = db_pool

    async def get_members_without_push_rules(self) -> list[RoomMember]:
        def select(txn: LoggingTransaction):
            sql = (
                "SELECT pc.room_id, m.member AS user_name "
                "FROM connect.people_conversations pc "
                "CROSS JOIN LATERAL unnest(pc.members) AS m(member) "
                "WHERE cardinality(pc.members) > 2 "
                "AND NOT EXISTS ( "
                "  SELECT 1 FROM public.push_rules pr "
                "  WHERE pr.user_name = m.member "
                "  AND split_part(pr.rule_id, ';', 2) = pc.room_id "
                ") "
                "ORDER BY pc.room_id, m.member"
            )
            txn.execute(sql)
            return [RoomMember(*row) for row in txn]

        return await self.db_pool.runInteraction(
            "get_members_without_push_rules",
            select,
            db_autocommit=True,
        )
