"""Audiences auth."""

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING

from synapse.api.errors import AuthError, Codes
from synapse.storage.database import LoggingTransaction
from synapse.types import UserID

from .power_levels_helpers import PowerLevelsHelpers

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class AudiencesAuth:
    """A class that authorizes audiences requests before they are sent to the audiences service."""

    def __init__(self, hs: "HomeServer", bot_ids: list[str]):
        self.power_levels_helpers = PowerLevelsHelpers(hs.get_module_api())
        self.store = hs.get_datastores().main
        self.bot_ids = bot_ids

    async def raise_if_all_human_admins_removed_after_update(
        self,
        room_id: str,
        current_extra_users_json: list[dict[str, str]],
        updated_extra_users_json: list[dict[str, str]],
    ):
        localparts = await self._get_human_admin_localparts(room_id)
        if not localparts:
            # Don't raise on rooms associated with corporate events,
            # which are intentionally designed to never have human admins
            return

        human_admin_external_ids = await self._get_human_admin_external_ids(localparts)
        current_external_ids = {json["externalId"] for json in current_extra_users_json}
        updated_external_ids = {json["externalId"] for json in updated_extra_users_json}

        if human_admin_external_ids - (current_external_ids - updated_external_ids):
            return

        raise AuthError(
            HTTPStatus.FORBIDDEN,
            "Cannot remove this user, because they're the only admin in this room",
            Codes.FORBIDDEN,
        )

    async def _get_human_admin_localparts(self, room_id: str) -> list[str]:
        current_members = set(await self.store.get_local_users_in_room(room_id))
        power_levels = await self.power_levels_helpers.get(room_id)
        users_power_levels = power_levels.content.get("users", {}).items()
        human_admin_ids = {
            user_id
            for (user_id, power_level) in users_power_levels
            if (user_id not in self.bot_ids) and (power_level == 100)
        }
        return [
            UserID.from_string(admin_id).localpart
            for admin_id in current_members & human_admin_ids
        ]

    async def _get_human_admin_external_ids(self, localparts: list[str]) -> set[str]:
        def select(txn: LoggingTransaction):
            sql = "SELECT data->>'externalId' FROM connect.profiles WHERE user_id = ANY(?)"
            txn.execute(sql, (localparts,))
            return [r[0] for r in txn]

        return set(
            await self.store.db_pool.runInteraction(
                "_get_human_admin_external_ids", select, db_autocommit=True
            )
        )
