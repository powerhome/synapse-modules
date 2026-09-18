"""Cross-worker claims on in-flight room audience evaluations.

Serializing evaluations of a room per worker process is not enough: the
evaluation request can be served by any Synapse worker replica (the CPG's
retry after a timeout, or an unarchive restore, can land on a different
replica than the one still evaluating). The claim therefore lives in
Postgres — the one store every worker shares — with the same semantics as
the CPG's per-room Redis lock:

- ``acquire`` takes the claim if the room is unclaimed or the previous
  claim expired (a crashed worker must not block a room forever).
- ``request_rerun`` coalesces a request arriving mid-evaluation into a
  single follow-up run by the current holder.
- ``heartbeat`` extends a live claim; evaluations legitimately run for
  minutes, so the TTL is kept short and renewed rather than sized to the
  slowest imaginable evaluation.
- ``release_if_no_rerun``/``release``/``consume_rerun`` are compare-on-token
  so a worker never releases (or reruns for) a claim it lost. The holder's
  normal exit is ``release_if_no_rerun`` — deleting the claim and checking
  the rerun flag in one statement, so a request that coalesces between the
  holder's last rerun check and its release cannot be deleted unrun.
"""

import time

TABLE = "connect.audiences_evaluation_claims"


def _now_ms() -> int:
    return int(time.time() * 1000)


class EvaluationClaims:
    """Postgres-backed per-room claim shared by all Synapse workers."""

    def __init__(self, store):
        self._db_pool = store.db_pool

    async def acquire(self, room_id: str, token: str, ttl_ms: int) -> bool:
        """Takes the room's claim.

        Atomic insert-or-take-over: succeeds when the room has no claim or
        the existing claim has expired.

        Args:
            room_id (str): The room whose evaluation is being claimed.
            token (str): Caller-generated value identifying this holder.
            ttl_ms (int): How long the claim lives without a heartbeat.

        Returns:
            bool: True when this token now holds the claim.
        """

        def txn_fn(txn):
            now = _now_ms()
            txn.execute(
                f"""
                INSERT INTO {TABLE}
                    (room_id, token, claimed_at_ms, expires_at_ms, rerun_requested)
                VALUES (?, ?, ?, ?, FALSE)
                ON CONFLICT (room_id) DO UPDATE SET
                    token = EXCLUDED.token,
                    claimed_at_ms = EXCLUDED.claimed_at_ms,
                    expires_at_ms = EXCLUDED.expires_at_ms,
                    rerun_requested = FALSE
                WHERE {TABLE}.expires_at_ms < ?
                """,
                (room_id, token, now, now + ttl_ms, now),
            )
            return txn.rowcount == 1

        return await self._db_pool.runInteraction(
            "audiences_evaluation_claim_acquire", txn_fn
        )

    async def request_rerun(self, room_id: str) -> bool:
        """Flags the live claim for a follow-up run.

        Args:
            room_id (str): The room whose claim should be flagged.

        Returns:
            bool: True when a live claim was flagged; False when there is no
                live claim (caller should retry ``acquire``).
        """

        def txn_fn(txn):
            txn.execute(
                f"""
                UPDATE {TABLE} SET rerun_requested = TRUE
                WHERE room_id = ? AND expires_at_ms >= ?
                """,
                (room_id, _now_ms()),
            )
            return txn.rowcount == 1

        return await self._db_pool.runInteraction(
            "audiences_evaluation_claim_request_rerun", txn_fn
        )

    async def consume_rerun(self, room_id: str, token: str) -> bool:
        """Clears the rerun flag, only for the current holder.

        Args:
            room_id (str): The room whose rerun flag should be consumed.
            token (str): The holder's token; other tokens consume nothing.

        Returns:
            bool: True when a pending rerun was consumed by its holder.
        """

        def txn_fn(txn):
            txn.execute(
                f"""
                UPDATE {TABLE} SET rerun_requested = FALSE
                WHERE room_id = ? AND token = ? AND rerun_requested = TRUE
                """,
                (room_id, token),
            )
            return txn.rowcount == 1

        return await self._db_pool.runInteraction(
            "audiences_evaluation_claim_consume_rerun", txn_fn
        )

    async def heartbeat(self, room_id: str, token: str, ttl_ms: int) -> bool:
        """Extends a live claim.

        Args:
            room_id (str): The room whose claim should be extended.
            token (str): The holder's token; other tokens extend nothing.
            ttl_ms (int): How long the extended claim lives.

        Returns:
            bool: True when the claim was extended; False when it was lost
                (expired and taken over) — the caller must stop evaluating.
        """

        def txn_fn(txn):
            txn.execute(
                f"""
                UPDATE {TABLE} SET expires_at_ms = ?
                WHERE room_id = ? AND token = ?
                """,
                (_now_ms() + ttl_ms, room_id, token),
            )
            return txn.rowcount == 1

        return await self._db_pool.runInteraction(
            "audiences_evaluation_claim_heartbeat", txn_fn
        )

    async def release_if_no_rerun(self, room_id: str, token: str) -> bool:
        """Deletes the claim when this token holds it and no rerun is pending.

        The delete and the rerun check are one statement, so a rerun
        requested at any point before the release — including between the
        holder's last ``consume_rerun`` and this call — makes the release
        fail instead of being silently discarded.

        Args:
            room_id (str): The room whose claim should be released.
            token (str): The holder's token; other tokens delete nothing.

        Returns:
            bool: True when the claim was deleted. False when it was not:
                either a rerun is pending (consume it and run again) or the
                claim was lost to a take-over.
        """

        def txn_fn(txn):
            txn.execute(
                f"""
                DELETE FROM {TABLE}
                WHERE room_id = ? AND token = ? AND rerun_requested = FALSE
                """,
                (room_id, token),
            )
            return txn.rowcount == 1

        return await self._db_pool.runInteraction(
            "audiences_evaluation_claim_release_if_no_rerun", txn_fn
        )

    async def release(self, room_id: str, token: str) -> None:
        """Deletes the claim if this token still holds it, rerun or not.

        Failure-path cleanup only: it deliberately discards a pending rerun,
        which is safe there because the failure propagates as a non-2xx
        response and the caller's retry re-covers the coalesced request. The
        holder's normal exit is ``release_if_no_rerun``.

        Args:
            room_id (str): The room whose claim should be released.
            token (str): The holder's token; other tokens delete nothing.
        """

        def txn_fn(txn):
            txn.execute(
                f"DELETE FROM {TABLE} WHERE room_id = ? AND token = ?",
                (room_id, token),
            )

        await self._db_pool.runInteraction("audiences_evaluation_claim_release", txn_fn)
