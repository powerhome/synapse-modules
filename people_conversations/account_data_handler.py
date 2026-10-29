"""Custom account data handler module."""

from synapse.handlers.account_data import AccountDataHandler
from synapse.storage._base import db_to_json
from synapse.storage.database import LoggingTransaction
from synapse.types import JsonDict, StreamKeyType


class ConnectAccountDataHandler(AccountDataHandler):
    """A custom handler for m.direct account data."""

    async def append_m_direct_account_data_for_user(
        self, user_id: str, dm_user_id: str, room_id: str
    ):
        assert self._store._can_write_to_account_data

        async with self._store._account_data_id_gen.get_next() as next_id:
            content = await self._store.db_pool.runInteraction(
                "append_m_direct_account_data_for_user",
                self._append_m_direct_account_data_for_user,
                next_id,
                user_id,
                dm_user_id,
                room_id,
            )

            self._store._account_data_stream_cache.entity_has_changed(user_id, next_id)
            self._store.get_global_account_data_for_user.invalidate((user_id,))
            self._store.get_global_account_data_by_type_for_user.invalidate(
                (user_id, "m.direct")
            )

        max_stream_id = self._store._account_data_id_gen.get_current_token()
        self._notifier.on_new_event(
            StreamKeyType.ACCOUNT_DATA, max_stream_id, users=[user_id]
        )
        await self._notify_modules(user_id, None, "m.direct", content)

    def _append_m_direct_account_data_for_user(
        self,
        txn: LoggingTransaction,
        next_id: int,
        user_id: str,
        dm_user_id: str,
        room_id: str,
    ) -> JsonDict:
        sql = """
            INSERT INTO
                account_data (user_id, account_data_type, stream_id, content)
            VALUES
                (?, 'm.direct', ?, jsonb_build_object(?, to_jsonb(ARRAY[?])))
            ON CONFLICT (user_id, account_data_type) DO
            UPDATE
            SET
                stream_id = ?,
                content = jsonb_set(
                    account_data.content::jsonb,
                    ARRAY[?],
                    to_jsonb(
                        (
                            SELECT
                                array_agg(DISTINCT r)
                            FROM
                                (
                                    SELECT
                                        jsonb_array_elements_text(account_data.content::jsonb -> ?) AS r
                                    UNION
                                    SELECT
                                        ?
                                ) AS room_list
                        )
                    ),
                    TRUE
                )
            RETURNING
                content
        """
        parameters = (
            user_id,
            next_id,
            dm_user_id,
            room_id,
            next_id,
            dm_user_id,
            dm_user_id,
            room_id,
        )
        txn.execute(sql, parameters)
        assert txn.rowcount == 1
        row = txn.fetchone()
        return db_to_json(row[0])
