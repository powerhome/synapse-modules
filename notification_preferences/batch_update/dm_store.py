"""Store for DM batch update queries."""
from __future__ import annotations

from synapse.storage.database import DatabasePool, LoggingTransaction


class DMBatchUpdateStore:
    """Database queries for DM push rule batch operations."""

    def __init__(self, db_pool: DatabasePool):
        self.db_pool = db_pool

    async def get_all_dm_push_rules(self) -> list[dict]:
        def select(txn: LoggingTransaction):
            sql = (
                "SELECT split_part(rule_id, ';', 2) AS room_id, "
                "pc.creator AS dm_creator, "
                "pr.* "
                "FROM public.push_rules pr "
                "JOIN connect.people_conversations pc "
                "ON split_part(pr.rule_id, ';', 2) = pc.room_id "
                "WHERE cardinality(pc.members) = 2 "
                "ORDER BY room_id, pr.user_name"
            )
            txn.execute(sql)
            columns = [desc[0] for desc in txn.description]
            return [dict(zip(columns, row)) for row in txn]

        results = await self.db_pool.runInteraction(
            "get_all_dm_push_rules", select, db_autocommit=True
        )
        return results
