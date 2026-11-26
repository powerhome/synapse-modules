"""Module for UserHelpers class"""

from typing import Collection, Set

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
    async def get_deactivated_among(
        cls, db_pool: DatabasePool, user_ids: Collection[str]
    ) -> Set[str]:
        """Return which of the given users are deactivated or pending deactivation.

        Args:
            db_pool (DatabasePool): The main store's database pool.
            user_ids (Collection[str]): The user ids to check.

        Returns:
            Set[str]: The subset of user_ids that is deactivated or pending deactivation.
        """
        deactivated = await db_pool.simple_select_many_batch(
            table="users",
            column="name",
            iterable=user_ids,
            retcols=("name",),
            keyvalues={"deactivated": 1},
            desc="get_deactivated_among",
        )
        pending = await db_pool.simple_select_many_batch(
            table="users_pending_deactivation",
            column="user_id",
            iterable=user_ids,
            retcols=("user_id",),
            desc="get_pending_deactivation_among",
        )
        return {name for (name,) in deactivated} | {user_id for (user_id,) in pending}

    @classmethod
    async def is_user_deactivated(cls, main_store: DataStore, user_id: str) -> bool:
        user_deactivated = await main_store.get_user_deactivated_status(user_id)
        if user_deactivated:
            return True

        user_pending_deactivation = await main_store.db_pool.simple_select_one_onecol(
            "users_pending_deactivation",
            keyvalues={"user_id": user_id},
            retcol="user_id",
            allow_none=True,
            desc="get_user_pending_deactivation",
        )

        return user_pending_deactivation is not None
