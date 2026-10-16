"""Tool for repairing m.direct account data."""

import logging

from synapse.module_api import ModuleApi
from synapse.storage.database import LoggingTransaction
from twisted.internet import defer

logger = logging.getLogger(__name__)


class AccountDataRepairer:
    """A module that repairs m.direct account data.

    When a client creates a people conversation, it updates each member's account data in separate
    requests. There is a chance for a request to fail for at least one member because Synapse is
    overloaded or there is a network issue.

    If at least some of the account data has been set correctly, this module fills in the rest.
    """

    def __init__(self, api: ModuleApi):
        is_worker = api.worker_name is not None
        if is_worker:
            logger.info(f"Not initializing repairer on worker: {api.worker_name}")
            return

        self.processing: defer.Deferred = None
        self.db_pool = api._hs.get_datastores().main.db_pool
        self.account_data_manager = api.account_data_manager

        if not self.processing or self.processing.called:
            self.processing = api.run_as_background_process(
                "repair_account_data", self._repair_account_data
            )

    async def _repair_account_data(self):
        records = await self._fetch_people_conversations_with_missing_account_data()
        logger.info(f"Repairing {len(records)} account data records")

        for (
            creator_id,
            people_conversation_id,
            first_invitee_id,
            members_with_missing_account_data,
        ) in records:
            for member_id in members_with_missing_account_data:
                if member_id == creator_id:
                    await self._mark_room_as_direct_message(
                        creator_id, first_invitee_id, people_conversation_id
                    )
                else:
                    await self._mark_room_as_direct_message(
                        member_id, creator_id, people_conversation_id
                    )

    async def _fetch_people_conversations_with_missing_account_data(
        self,
    ) -> list[tuple[str, str, str, set[str]]]:
        def select(txn: LoggingTransaction):
            txn.execute(
                """
                WITH people_conversations_from_ad AS (
                    SELECT
                        room_id,
                        array_agg(DISTINCT user_id ORDER BY user_id) AS members_with_correct_account_data
                    FROM
                        account_data,
                        jsonb_each(content::jsonb) AS each_entry (content_user_id, content_room_ids),
                        jsonb_array_elements_text(content_room_ids) AS room_id
                    WHERE
                        account_data_type = 'm.direct'
                    GROUP BY
                        room_id
                )
                SELECT
                    pc.creator,
                    ad.room_id,
                    pc.first_invitee,
                    ad.members_with_correct_account_data,
                    pc.members
                FROM
                    people_conversations_from_ad ad
                    JOIN connect.people_conversations pc ON ad.room_id = pc.room_id
                WHERE
                    ad.members_with_correct_account_data <> pc.members
                """,
            )
            # the set difference is members that have missing account data
            return [(r[0], r[1], r[2], set(r[4]) - set(r[3])) for r in txn]

        return await self.db_pool.runInteraction(
            "fetch_people_conversations_with_missing_account_data",
            select,
            db_autocommit=True,
        )

    # see https://github.com/matrix-org/synapse-auto-accept-invite/blob/main/synapse_auto_accept_invite/__init__.py#L134
    async def _mark_room_as_direct_message(
        self, user_id: str, dm_user_id: str, room_id: str
    ):
        dm_map: dict[str, tuple[str, ...]] = dict(
            await self.account_data_manager.get_global(user_id, "m.direct") or {}
        )

        if dm_user_id not in dm_map:
            dm_map[dm_user_id] = (room_id,)
        else:
            dm_rooms_for_user = dm_map[dm_user_id]
            assert isinstance(dm_rooms_for_user, (tuple, list))
            dm_map[dm_user_id] = tuple(dm_rooms_for_user) + (room_id,)

        await self.account_data_manager.put_global(user_id, "m.direct", dm_map)
