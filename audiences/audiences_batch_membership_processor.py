"""Module for AudiencesBatchMembershipProcessor class, applying individual Synapse membership changes based on changes to audiences."""

import logging
import secrets
from typing import Any, Dict

from synapse.api.constants import Membership
from synapse.api.errors import (
    HttpResponseException,
    LimitExceededError,
    UnstableSpecAuthError,
)
from synapse.module_api import ModuleApi

from ..helpers.request_context import cpg_headers, init_request_context, log_event
from ..helpers.user import UserHelpers
from .evaluation_claims import EvaluationClaims

logger = logging.getLogger(__name__)

# How long a claim lives without a heartbeat. Kept short — a crashed worker
# must not block its room for long — and renewed while the evaluation runs,
# rather than sized to the slowest imaginable evaluation.
CLAIM_TTL_MS = 120_000
# Renew the claim every N membership changes (each change is one Synapse
# event-creation round trip, so N changes is well under CLAIM_TTL_MS).
CLAIM_HEARTBEAT_EVERY = 25
CLAIM_ACQUIRE_ATTEMPTS = 3


class EvaluationClaimLost(RuntimeError):
    """This worker's claim expired mid-evaluation and was taken over.

    Continuing would mean two workers evaluating the same room, so the
    evaluation aborts; it propagates as a non-2xx response and the CPG job
    retries against whichever worker now holds the claim.
    """


class AudiencesBatchMembershipProcessor:
    """This class applies individual room membership changes in Synapse, based on changes to audiences.

    The Matrix spec (and Synapse API) has two fundamental limitations related to room membership changes:
    1) In at least some cases, members cannot add another user to a room (they must instead invite them).
    2) The Matrix API only allows a single membership change per HTTP request.

    These lead to slow performance in cases where many users need to be added to or removed from a room.

    To improve performance when batch membership changes are applied, this class applies membership changes via
    the Synapse module_api which is significantly faster than the Matrix API (via HTTP requests).

    Attributes:
        config (Dict[str, Any]): Configuration dictionary containing bot_user_ids, idp_id, and hs_token.
        api (ModuleApi): An instance of the Synapse ModuleApi used to interact with Synapse.
    """

    def __init__(self, config: Dict[str, Any], api: ModuleApi):
        self.bot_user_ids = config["bot_user_ids"]
        self._api = api

        self.idp_id = f"oidc-{config['idp_id']}"
        self.store = api._store
        self.hs_token = config["hs_token"]
        self.audiences_bot_user_id = config["audiences_bot_user_id"]

        # Cross-worker per-room claim (Postgres-backed). Serializes
        # evaluations of a room across ALL worker replicas and entry points
        # (the CPG's PUT and the unarchive restore) — not just within this
        # process. The CPG's per-room Redis lock normally prevents concurrent
        # requests, but its guarantee breaks when the synchronous PUT times
        # out (the evaluation keeps running server-side while the lock is
        # released and the retry lands on another replica); this claim is the
        # defense that holds when that happens.
        self._claims = EvaluationClaims(self.store)
        self._claim_room: str | None = None
        self._claim_token: str | None = None

    async def process_batch_memberships(
        self, room_id: str, request_id: str | None = None
    ):
        """
        Processes batches of membership changes.

        Concurrent evaluations of the same room would diff against different
        membership snapshots and can flap users (one run kicks while the other
        invites), so per room only one evaluation runs at a time; requests
        arriving meanwhile coalesce into a single re-run afterwards. The guard
        is a Postgres-backed claim shared by every worker replica and every
        entry point (see EvaluationClaims).

        A failed evaluation propagates out of the claim runner, so the servlet
        returns a non-2xx response and the CPG job retries (the retry
        re-covers any request that coalesced into the failed run).

        Args:
            room_id (str): The ID of the room.
            request_id (str): CPG request ID captured before scheduling; re-hydrated here for tracing.
        """
        init_request_context(request_id=request_id, action="batch_membership_changed")
        await self._run_with_claim(room_id, self._process_batch_memberships_once)

    async def _run_with_claim(self, room_id: str, run_once) -> None:
        """Runs one evaluation pass under the cross-worker per-room claim.

        A request that finds the claim held coalesces: it flags the holder
        for exactly one follow-up run and returns. Follow-up runs are always
        full evaluations (they read the latest desired membership), so they
        cover whatever the coalesced request wanted — including restores.

        Args:
            room_id (str): The ID of the room.
            run_once: Coroutine evaluating the room once; reruns always run full.

        Raises:
            RuntimeError: The claim could neither be acquired nor coalesced.
            Exception: Re-raised evaluation failure; the servlet returns non-2xx.
        """
        token = secrets.token_hex(16)
        acquired = False
        for _ in range(CLAIM_ACQUIRE_ATTEMPTS):
            if await self._claims.acquire(room_id, token, CLAIM_TTL_MS):
                acquired = True
                break
            if await self._claims.request_rerun(room_id):
                log_event(
                    logger,
                    "Batch membership processing coalesced into in-flight evaluation",
                    room_id=room_id,
                )
                return
            # The claim expired between the two checks; retry the take-over.
        if not acquired:
            # Pathological contention. Fail the request so the caller's retry
            # (the CPG job) re-drives the evaluation rather than silently
            # dropping it.
            raise RuntimeError(
                f"could not claim or coalesce membership evaluation for {room_id}"
            )

        self._claim_room = room_id
        self._claim_token = token
        try:
            while True:
                await run_once(room_id)
                # The release checks the rerun flag in the same statement it
                # deletes, so a request that coalesced at any point before it
                # — even just after the run finished — fails the release and
                # gets covered by another pass, instead of its flag being
                # deleted unrun after its caller was already told "coalesced".
                if await self._claims.release_if_no_rerun(room_id, token):
                    break
                if not await self._claims.consume_rerun(room_id, token):
                    # Release failed yet there is no rerun to consume: the
                    # claim expired and was taken over. The new holder runs a
                    # full evaluation, so there is nothing left to cover here.
                    break
                # Reruns are full evaluations regardless of the initial pass.
                run_once = self._process_batch_memberships_once
                log_event(
                    logger,
                    "Re-running batch membership processing for coalesced requests",
                    room_id=room_id,
                )
        except Exception:
            # A pending rerun (a request that coalesced into this run) is
            # dropped here, but the exception propagates to the servlet as a
            # non-2xx response, so the CPG job retries and the retried
            # evaluation reads the latest desired membership — covering the
            # coalesced request's change as well.
            log_event(
                logger,
                "Evaluation failed; any coalesced rerun is dropped and the "
                "caller's retry will re-cover it",
                room_id=room_id,
            )
            raise
        finally:
            self._claim_room = None
            self._claim_token = None
            await self._claims.release(room_id, token)

    async def _heartbeat_claim(self) -> None:
        if self._claim_room is None or self._claim_token is None:
            return
        still_held = await self._claims.heartbeat(
            self._claim_room, self._claim_token, CLAIM_TTL_MS
        )
        if not still_held:
            raise EvaluationClaimLost(
                f"evaluation claim for {self._claim_room} expired and was taken over"
            )

    async def _process_batch_memberships_once(self, room_id: str):
        log_event(logger, "Batch membership processing started", room_id=room_id)

        desired_room_members = await self._desired_room_members(room_id)
        invited, joined = await self._fetch_invited_and_joined_users(room_id)

        log_event(
            logger,
            "Computed current and desired room membership",
            room_id=room_id,
            invited_count=len(invited),
            joined_count=len(joined),
            desired_count=len(desired_room_members),
        )
        logger.debug(
            f"Room {room_id}: invited={invited} joined={joined} "
            f"desired={desired_room_members}"
        )

        # Users in `invited` are already on their way in (the auto-accept
        # module joins them); re-inviting them is pure wasted work.
        room_members_to_add = [
            mxid
            for mxid in desired_room_members
            if mxid and mxid not in joined and mxid not in invited
        ]
        log_event(
            logger,
            "Adding users to room (add)",
            room_id=room_id,
            action="add",
            to_add_count=len(room_members_to_add),
        )
        await self._process_batch_memberships_type(
            room_id, "invite", room_members_to_add
        )

        room_members_to_remove = [
            mxid
            for mxid in (invited | joined)
            if mxid
            and mxid not in desired_room_members
            and mxid not in self.bot_user_ids
        ]
        log_event(
            logger,
            "Removing users from room (remove)",
            room_id=room_id,
            action="remove",
            to_remove_count=len(room_members_to_remove),
        )
        await self._process_batch_memberships_type(
            room_id, "leave", room_members_to_remove
        )

        log_event(logger, "Batch membership processing completed", room_id=room_id)

    async def restore_memberships_from_audience_criteria(self, room_id: str) -> None:
        """
        Restores memberships in a room based on audience criteria data.

        Runs under the same cross-worker claim as regular evaluations, so an
        unarchive restore can never race a CPG evaluation of the same room.
        If an evaluation is already in flight, the restore coalesces into a
        follow-up full evaluation, which invites every desired member — a
        superset of what the restore would have done.

        Args:
            room_id (str): The ID of the room.
        """
        init_request_context(action="restore_memberships")
        await self._run_with_claim(room_id, self._restore_memberships_once)

    async def _restore_memberships_once(self, room_id: str) -> None:
        desired_room_members = await self._desired_room_members(room_id)

        log_event(
            logger,
            "Restoring room memberships (add)",
            room_id=room_id,
            action="add",
            desired_count=len(desired_room_members),
        )
        await self._process_batch_memberships_type(
            room_id, "invite", list(desired_room_members)
        )

    async def _fetch_invited_and_joined_users(
        self, room_id: str
    ) -> tuple[set[str], set[str]]:
        user_memberships = await self.store.get_local_users_related_to_room(room_id)
        invited = {
            user_mxid
            for (user_mxid, membership) in user_memberships
            if membership == Membership.INVITE
        }
        joined = {
            user_mxid
            for (user_mxid, membership) in user_memberships
            if membership == Membership.JOIN
        }
        return invited, joined

    async def _process_batch_memberships_type(
        self, room_id: str, kind: str, mxids: list[str]
    ):
        for index, mxid in enumerate(mxids):
            if index and index % CLAIM_HEARTBEAT_EVERY == 0:
                await self._heartbeat_claim()
            try:
                await self._api.update_room_membership(
                    sender=self.audiences_bot_user_id,
                    target=mxid,
                    room_id=room_id,
                    content=None,
                    new_membership=kind,
                )
                log_event(
                    logger,
                    "Synapse membership change applied",
                    room_id=room_id,
                    target=mxid,
                    new_membership=kind,
                    outcome="applied",
                )
            except UnstableSpecAuthError as e:
                if "already in the room" in str(e.msg):
                    log_event(
                        logger,
                        "Synapse membership change skipped; user already in room",
                        room_id=room_id,
                        target=mxid,
                        new_membership=kind,
                        outcome="already_in_room",
                    )
            except LimitExceededError as e:
                if e.retry_after_ms is not None:
                    # .. See https://github.com/matrix-org/synapse/issues/6286#issuecomment-646944920 and
                    # .. https://github.com/matrix-org/synapse/pull/9648
                    log_event(
                        logger,
                        "Synapse rate limit exceeded; retrying membership change",
                        level=logging.WARNING,
                        room_id=room_id,
                        target=mxid,
                        new_membership=kind,
                        retry_after_ms=e.retry_after_ms,
                        detail=(
                            f"{e.msg}. Is {self.audiences_bot_user_id} in the "
                            "ratelimit_override table?"
                        ),
                    )
                    time_to_sleep = e.retry_after_ms / 1000
                    await self._api.sleep(time_to_sleep)
                    await self._api.update_room_membership(
                        sender=self.audiences_bot_user_id,
                        target=mxid,
                        room_id=room_id,
                        content=None,
                        new_membership=kind,
                    )
                    log_event(
                        logger,
                        "Synapse membership change applied after retry",
                        room_id=room_id,
                        target=mxid,
                        new_membership=kind,
                        outcome="applied_after_retry",
                    )

    async def _desired_room_members(self, room_id: str) -> set[str]:
        """
        Returns the desired room members for the provided event.

        Args:
            room_id (str): The room id.

        Returns:
            list[str]: The desired room members.
        """
        subs = await self._get_subs(room_id)

        results = await self.store.db_pool.simple_select_many_batch(
            "user_external_ids",
            column="external_id",
            iterable=subs,
            retcols={"user_id"},
            keyvalues={"auth_provider": self.idp_id},
        )
        # Omitting deactivated users from desired will allow Audiences to
        # kick deactivated users but not add them
        deactivated_users = await UserHelpers.get_deactivated_users(self.store.db_pool)
        return {mxid for (mxid,) in results if mxid not in deactivated_users}

    async def _get_subs(self, room_id: str) -> list[str]:
        path = "http://audiences:3000/audiences/api/subs"

        headers = {
            b"Authorization": [b"Bearer " + self.hs_token.encode("ascii")],
            **cpg_headers(),
        }

        log_event(
            logger,
            "Fetching audience subs from CPG",
            room_id=room_id,
            endpoint="/audiences/api/subs",
        )
        try:
            response = await self._api.http_client.get_json(
                uri=path,
                headers=headers,
                args={"room_mxid": room_id},
            )
        except HttpResponseException as e:
            log_event(
                logger,
                "Failed to fetch audience subs from CPG",
                level=logging.ERROR,
                room_id=room_id,
                status=e.code,
            )
            raise e.to_synapse_error() from e

        assert response["room_mxid"] == room_id
        log_event(
            logger,
            "Fetched audience subs from CPG",
            room_id=room_id,
            sub_count=len(response["subs"]),
        )
        return response["subs"]
