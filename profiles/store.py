"""Profiles store."""

from http import HTTPStatus

from synapse.api.errors import Codes, StoreError
from synapse.storage.database import DatabasePool, LoggingTransaction
from synapse.types import JsonDict, UserID

PROFILES_QUERY = """
    SELECT
        pp.user_id AS localpart,
        cp.active,
        pp.displayname AS display_name,
        pp.avatar_url,
        cp.data ->> 'title' AS job_title,
        cp.data -> 'urn:ietf:params:scim:schemas:extension:authservice:2.0:User' ->> 'department' AS department,
        cp.data -> 'urn:ietf:params:scim:schemas:extension:authservice:2.0:User' ->> 'territory' AS territory,
        cp.data -> 'urn:ietf:params:scim:schemas:extension:authservice:2.0:User' ->> 'territoryAbbr' AS territory_abbreviation
    FROM
        profiles pp JOIN connect.profiles cp ON pp.user_id = cp.user_id
"""

ACTIVE_LISTED_PROFILES_QUERY = f"{PROFILES_QUERY} WHERE cp.active = TRUE AND cp.data -> 'urn:ietf:params:scim:schemas:extension:authservice:2.0:User' ->> 'department' != 'BT Systems'"


class ProfileStore:
    """A class that handles profile database queries."""

    def __init__(self, db_pool: DatabasePool):
        self.db_pool = db_pool

    async def get_profiles(self) -> dict[str, JsonDict]:
        def select(txn: LoggingTransaction):
            txn.execute(PROFILES_QUERY)
            rows = txn.fetchall()

            column_headers = tuple(column[0] for column in txn.description)
            return column_headers, rows

        domain = self.db_pool.hs.hostname
        column_headers, rows = await self.db_pool.runInteraction(
            "get_profiles", select, db_autocommit=True
        )
        return {
            UserID(row[0], domain).to_string(): dict(zip(column_headers, row))
            for row in rows
        }

    async def get_listed_profiles(self) -> dict[str, JsonDict]:
        def select(txn: LoggingTransaction):
            txn.execute(ACTIVE_LISTED_PROFILES_QUERY)
            rows = txn.fetchall()

            column_headers = tuple(column[0] for column in txn.description)
            return column_headers, rows

        domain = self.db_pool.hs.hostname
        column_headers, rows = await self.db_pool.runInteraction(
            "get_profiles", select, db_autocommit=True
        )
        return {
            UserID(row[0], domain).to_string(): dict(zip(column_headers, row))
            for row in rows
        }

    async def get_profile(self, user_id: str) -> JsonDict:
        localpart = UserID.from_string(user_id).localpart

        def select(txn: LoggingTransaction):
            txn.execute(f"{PROFILES_QUERY} WHERE pp.user_id = ?", (localpart,))
            row = txn.fetchone()

            if not row:
                raise StoreError(
                    HTTPStatus.NOT_FOUND,
                    f"profile for {user_id} not found",
                    errcode=Codes.NOT_FOUND,
                )
            if txn.rowcount > 1:
                raise StoreError(
                    HTTPStatus.INTERNAL_SERVER_ERROR, f"too many rows for {localpart}"
                )

            column_headers = tuple(column[0] for column in txn.description)
            return column_headers, row

        column_headers, row = await self.db_pool.runInteraction(
            "get_profile", select, db_autocommit=True
        )
        return dict(zip(column_headers, row))
