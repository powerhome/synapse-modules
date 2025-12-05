"""Message fetching by rooms"""

from typing import Optional

from synapse.api.errors import SynapseError


class MessageQuery:
    """Fetches encrypted messages from the database from a list of room IDs."""

    @classmethod
    async def get_encrypted_messages(
        cls,
        db_pool,
        room_ids: list[str],
        pagination_token: Optional[str] = None,
    ) -> list[dict[str, any]]:
        pagination_clause = ""
        origin_server_ts = None
        stream = None

        if pagination_token:
            try:
                origin_server_ts_str, stream_str = pagination_token.split(",")
                origin_server_ts = int(origin_server_ts_str)
                stream = int(stream_str)
            except Exception:
                raise SynapseError(400, "Invalid pagination token")

            pagination_clause = """
                (origin_server_ts < %(origin_server_ts)s OR (origin_server_ts = %(origin_server_ts)s AND stream_ordering < %(stream)s)) AND
            """

        # WARNING: The below string interpolates the above pagination substring
        # which is safe, but has to disable the S608 lint rule to do this. Be
        # extremely careful about adding any extra violations that may use user
        # input.
        sql = f"""
            SELECT
                e.event_id,
                e.room_id,
                ej.json::json AS event_json,
                e.origin_server_ts,
                e.stream_ordering
            FROM
                events e
            JOIN
                event_json ej ON e.event_id = ej.event_id
            WHERE
                {pagination_clause}
                e.type = 'm.room.message' AND e.room_id = ANY(%(room_ids)s)
            ORDER BY
                origin_server_ts DESC NULLS LAST, stream_ordering DESC NULLS LAST;
        """  # noqa: S608

        records = await db_pool.runInteraction(
            "fetch_encrypted_messages",
            cls._execute_sql_query,
            sql,
            room_ids,
            origin_server_ts,
            stream,
        )
        return records

    @staticmethod
    def _execute_sql_query(
        txn,
        sql_query: str,
        room_ids: list[str],
        origin_server_ts: Optional[int],
        stream: Optional[int],
    ) -> list[dict[str, any]]:
        txn.execute(
            sql_query,
            {
                "room_ids": room_ids,
                "origin_server_ts": origin_server_ts,
                "stream": stream,
            },
        )
        rows = txn.fetchall()
        if not rows:
            return []

        columns = [desc[0] for desc in txn.description]
        return [dict(zip(columns, row)) for row in rows]
