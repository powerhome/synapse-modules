"""Tears down the users a run (or every run) provisioned."""

import json
import logging

from synapse.types import create_requester

from connect.fixtures.markers import RUN_MARKER, owner_from_marker

logger = logging.getLogger(__name__)


class Cleanup:
    """Deactivates and erases fixtures users, selected by their run marker.

    Every fixtures user carries the ``RUN_MARKER`` account-data tagging its
    owning run, so teardown finds a run's users by that marker — including
    Fixed-name users, which carry no ``fx_`` localpart prefix. The localpart
    prefix is a belt-and-suspenders fallback for a Random user that crashed
    between register and marker-write. The marker is written only by this module,
    so cleanup can never touch a real or populator-seeded account.

    Deactivation with ``erase_data`` is Synapse's supported user-removal path;
    the rows (and the marker — ``erase_data`` does not purge global account data)
    remain, but the accounts are dead and unusable, which is what stops test
    users accumulating as live accounts. Selection filters to active users so
    re-running is idempotent (a second call deactivates nothing).
    """

    def __init__(self, hs):
        self.hs = hs
        self.store = hs.get_datastores().main
        self.deactivate_handler = hs.get_deactivate_account_handler()

    async def cleanup_run(self, run_id: str, run_prefix: str) -> int:
        """Deactivate+erase the active users owned by ``run_id``.

        Args:
            run_id (str): The run whose users to tear down (matched by marker).
            run_prefix (str): The run's localpart prefix, matched as a fallback for a Random user missing its marker.

        Returns:
            int: The number of users deactivated.
        """
        by_marker = [
            mxid for mxid, owner in await self._select_marked() if owner == run_id
        ]
        like = f"@{run_prefix}%:{self.hs.hostname}"
        by_prefix = await self.store.db_pool.runInteraction(
            "fixtures_select_run_users", self._select_by_prefix_txn, like
        )
        return await self.deactivate(self._unique(by_marker + by_prefix))

    async def cleanup_all(self) -> int:
        """Deactivate+erase every active fixtures user, across all runs.

        Returns:
            int: The number of users deactivated.
        """
        by_marker = [mxid for mxid, _ in await self._select_marked()]
        like = f"@fx_%:{self.hs.hostname}"
        by_prefix = await self.store.db_pool.runInteraction(
            "fixtures_select_all_fx_users", self._select_by_prefix_txn, like
        )
        return await self.deactivate(self._unique(by_marker + by_prefix))

    async def inventory(self) -> list:
        """The active fixtures users, each tagged with the run that owns it.

        Read-only: surfaces what is currently provisioned without tearing
        anything down. Marker-based, so every user maps to a known run.

        Returns:
            list: ``(mxid, run_id)`` for every active marked fixtures user.
        """
        return await self._select_marked()

    async def _select_marked(self) -> list:
        # The active users carrying the run marker, as (mxid, owning_run_id).
        return await self.store.db_pool.runInteraction(
            "fixtures_select_marked_users", self._select_marked_txn
        )

    async def deactivate(self, mxids: list[str]) -> int:
        # Deactivate+erase the given users. The public teardown primitive: run /
        # global cleanup pass the marker-selected set; scenario rollback passes
        # exactly the users one POST created.
        for mxid in mxids:
            await self.deactivate_handler.deactivate_account(
                mxid, erase_data=True, requester=create_requester(mxid)
            )
            logger.info("fixtures deactivated user %s", mxid)

        return len(mxids)

    @staticmethod
    def _unique(mxids: list[str]) -> list[str]:
        # De-dupe (a Random user matches both the marker and the prefix) while
        # keeping a stable order.
        return list(dict.fromkeys(mxids))

    @staticmethod
    def _select_by_prefix_txn(txn, like: str) -> list[str]:
        # Active users whose localpart matches the prefix; the deactivated filter
        # keeps re-runs idempotent.
        txn.execute(
            "SELECT name FROM users WHERE name LIKE ? AND deactivated = 0", (like,)
        )
        return [row[0] for row in txn]

    @staticmethod
    def _select_marked_txn(txn) -> list:
        # Active marked users as (mxid, owning_run_id). The active filter keeps
        # re-runs idempotent — deactivation leaves the marker in place, so
        # without it a torn-down user would re-report on the next call.
        txn.execute(
            "SELECT ad.user_id, ad.content FROM account_data ad "
            "JOIN users u ON u.name = ad.user_id "
            "WHERE ad.account_data_type = ? AND u.deactivated = 0",
            (RUN_MARKER,),
        )
        rows = []
        for user_id, content in txn:
            owner = None
            if content:
                try:
                    owner = owner_from_marker(json.loads(content))
                except (ValueError, TypeError):
                    owner = None
            rows.append((user_id, owner))
        return rows


class RoomCleanup:
    """Purges the rooms a run (or every run) provisioned.

    A fixtures room carries the ``RUN_MARKER`` ownership state event keyed by its
    owning ``run_id`` (set in the room's initial state). Teardown finds rooms by
    that state event — a single ``current_state_events`` lookup, no event-content
    parsing — and purges each via the pagination handler, Synapse's supported
    room-removal path. Purge removes the room's rows (the marker state event
    included), so a re-run finds nothing: cleanup is idempotent.
    """

    def __init__(self, hs):
        self.hs = hs
        self.store = hs.get_datastores().main
        self.pagination_handler = hs.get_pagination_handler()

    async def cleanup_run(self, run_id: str) -> int:
        """Purge the rooms owned by ``run_id``.

        Args:
            run_id (str): The run whose rooms to tear down (matched by marker).

        Returns:
            int: The number of rooms purged.
        """
        room_ids = await self.store.db_pool.runInteraction(
            "fixtures_select_run_rooms", self._select_by_run_txn, run_id
        )
        return await self.purge(room_ids)

    async def cleanup_all(self) -> int:
        """Purge every fixtures room, across all runs.

        Returns:
            int: The number of rooms purged.
        """
        room_ids = await self.store.db_pool.runInteraction(
            "fixtures_select_all_rooms", self._select_all_txn
        )
        return await self.purge(room_ids)

    async def inventory(self) -> list:
        """The fixtures rooms, each tagged with the run that owns it.

        Read-only: a single ``current_state_events`` read of the ownership
        marker, whose ``state_key`` is the owning run.

        Returns:
            list: ``(room_id, run_id)`` for every fixtures room.
        """
        return await self.store.db_pool.runInteraction(
            "fixtures_inventory_rooms", self._inventory_txn
        )

    async def purge(self, room_ids: list[str]) -> int:
        # Purge the given rooms. The public teardown primitive: run / global
        # cleanup pass the marker-selected set; scenario rollback passes exactly
        # the rooms one POST created. purge_room runs inline and takes per-room
        # write locks — fine for the small, few rooms a fixture run creates; move
        # to a background delete if seeded rooms ever grow large.
        for room_id in room_ids:
            await self.pagination_handler.purge_room(room_id, force=True)
            logger.info("fixtures purged room %s", room_id)

        return len(room_ids)

    @staticmethod
    def _select_by_run_txn(txn, run_id: str) -> list[str]:
        # Rooms whose ownership marker is keyed by this run. Each room has exactly
        # one such state event, so no de-dupe is needed.
        txn.execute(
            "SELECT room_id FROM current_state_events "
            "WHERE type = ? AND state_key = ?",
            (RUN_MARKER, run_id),
        )
        return [row[0] for row in txn]

    @staticmethod
    def _select_all_txn(txn) -> list[str]:
        # Every room carrying the ownership marker, across all runs.
        txn.execute(
            "SELECT DISTINCT room_id FROM current_state_events WHERE type = ?",
            (RUN_MARKER,),
        )
        return [row[0] for row in txn]

    @staticmethod
    def _inventory_txn(txn) -> list:
        # Each fixtures room carries one RUN_MARKER state event whose state_key
        # is the owning run, so the room->run mapping is a single read.
        txn.execute(
            "SELECT room_id, state_key FROM current_state_events WHERE type = ?",
            (RUN_MARKER,),
        )
        return [(room_id, run_id) for room_id, run_id in txn]
