"""Module for UserHelpers class"""

from typing import Set

from synapse.storage.database import DatabasePool
from synapse.storage.databases.main import DataStore


class UserHelpers:
    """A helper class for user related logic"""

    @classmethod
    async def get_deactivated_users(cls, db_pool: DatabasePool) -> Set[str]:
        deactivated_users = set(
            await db_pool.simple_select_onecol(
                table="users", keyvalues={"deactivated": 1}, retcol="name"
            )
        )

        users_pending_deactivation = set(
            await db_pool.simple_select_onecol(
                table="users_pending_deactivation", keyvalues=None, retcol="user_id"
            )
        )

        return deactivated_users | users_pending_deactivation

    @classmethod
    async def is_user_deactivated(cls, main_store: DataStore, user_id: str) -> bool:
        user_deactivated = await main_store.get_user_deactivated_status(user_id)
        user_pending_deactivation = (
            await main_store.db_pool.simple_select_one_onecol(
                "users_pending_deactivation",
                keyvalues={"user_id": user_id},
                retcol="user_id",
                allow_none=True,
                desc="get_user_pending_deactivation",
            )
            is not None
        )

        return user_deactivated or user_pending_deactivation
