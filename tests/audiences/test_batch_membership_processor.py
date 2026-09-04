"""Tests for AudiencesBatchMembershipProcessor diffing and coalescing."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from connect.audiences.audiences_batch_membership_processor import (
    AudiencesBatchMembershipProcessor,
    EvaluationClaimLost,
)

ROOM = "!room:localhost"

CONFIG = {
    "bot_user_ids": ["@connect_bot:localhost"],
    "idp_id": "nitro",
    "hs_token": "hs_token",  # noqa: S105
    "audiences_bot_user_id": "@audiences_bot:localhost",
}


class InMemoryClaims:
    """In-memory stand-in for the Postgres-backed EvaluationClaims.

    Same interface and semantics; `rooms` maps room_id to the claim state so
    tests can assert on it. Share one instance between processors to model
    the shared database.
    """

    def __init__(self):
        self.rooms = {}

    async def acquire(self, room_id, token, ttl_ms):
        claim = self.rooms.get(room_id)
        if claim is None or claim["expired"]:
            self.rooms[room_id] = {"token": token, "rerun": False, "expired": False}
            return True
        return False

    async def request_rerun(self, room_id):
        claim = self.rooms.get(room_id)
        if claim is None or claim["expired"]:
            return False
        claim["rerun"] = True
        return True

    async def consume_rerun(self, room_id, token):
        claim = self.rooms.get(room_id)
        if claim and claim["token"] == token and claim["rerun"]:
            claim["rerun"] = False
            return True
        return False

    async def heartbeat(self, room_id, token, ttl_ms):
        claim = self.rooms.get(room_id)
        return bool(claim and claim["token"] == token and not claim["expired"])

    async def release_if_no_rerun(self, room_id, token):
        claim = self.rooms.get(room_id)
        if claim and claim["token"] == token and not claim["rerun"]:
            del self.rooms[room_id]
            return True
        return False

    async def release(self, room_id, token):
        claim = self.rooms.get(room_id)
        if claim and claim["token"] == token:
            del self.rooms[room_id]


def make_processor(claims=None):
    api = MagicMock()
    api.update_room_membership = AsyncMock()
    processor = AudiencesBatchMembershipProcessor(CONFIG, api)
    processor._claims = claims or InMemoryClaims()
    return processor, api


def stub_membership(processor, desired, invited, joined):
    processor._desired_room_members = AsyncMock(return_value=set(desired))
    processor._fetch_invited_and_joined_users = AsyncMock(
        return_value=(set(invited), set(joined))
    )


def membership_changes(api):
    return [
        (call.kwargs["new_membership"], call.kwargs["target"])
        for call in api.update_room_membership.await_args_list
    ]


class DiffTestSuite(unittest.IsolatedAsyncioTestCase):
    """Desired-vs-actual membership diffing."""

    async def test_invites_only_users_neither_joined_nor_invited(self):
        processor, api = make_processor()
        stub_membership(
            processor,
            desired=["@a:hs", "@b:hs", "@c:hs"],
            invited=["@b:hs"],
            joined=["@a:hs"],
        )

        await processor.process_batch_memberships(ROOM)

        self.assertEqual(membership_changes(api), [("invite", "@c:hs")])

    async def test_kicks_members_no_longer_desired(self):
        processor, api = make_processor()
        stub_membership(
            processor,
            desired=["@a:hs"],
            invited=["@b:hs"],
            joined=["@a:hs", "@c:hs"],
        )

        await processor.process_batch_memberships(ROOM)

        self.assertEqual(
            sorted(membership_changes(api)),
            [("leave", "@b:hs"), ("leave", "@c:hs")],
        )

    async def test_never_kicks_bots(self):
        processor, api = make_processor()
        stub_membership(
            processor,
            desired=["@a:hs"],
            invited=[],
            joined=["@a:hs", "@connect_bot:localhost"],
        )

        await processor.process_batch_memberships(ROOM)

        api.update_room_membership.assert_not_awaited()

    async def test_stable_membership_makes_no_changes(self):
        processor, api = make_processor()
        stub_membership(
            processor,
            desired=["@a:hs", "@b:hs"],
            invited=["@b:hs"],
            joined=["@a:hs"],
        )

        await processor.process_batch_memberships(ROOM)

        api.update_room_membership.assert_not_awaited()


class CoalescingTestSuite(unittest.IsolatedAsyncioTestCase):
    """Per-room serialization and coalescing of concurrent evaluations."""

    async def test_requests_during_evaluation_coalesce_into_one_rerun(self):
        processor, _api = make_processor()
        release = asyncio.Event()
        runs = []

        async def evaluation(room_id):
            runs.append(room_id)
            if len(runs) == 1:
                await release.wait()

        processor._process_batch_memberships_once = evaluation

        first = asyncio.ensure_future(processor.process_batch_memberships(ROOM))
        await asyncio.sleep(0)
        # Three requests land while the first evaluation is still running.
        await processor.process_batch_memberships(ROOM)
        await processor.process_batch_memberships(ROOM)
        await processor.process_batch_memberships(ROOM)
        release.set()
        await first

        self.assertEqual(runs, [ROOM, ROOM])
        self.assertEqual(processor._claims.rooms, {})

    async def test_requests_after_completion_run_fresh(self):
        processor, _api = make_processor()
        runs = []

        async def evaluation(room_id):
            runs.append(room_id)

        processor._process_batch_memberships_once = evaluation

        await processor.process_batch_memberships(ROOM)
        await processor.process_batch_memberships(ROOM)

        self.assertEqual(runs, [ROOM, ROOM])

    async def test_rooms_do_not_block_each_other(self):
        processor, _api = make_processor()
        release = asyncio.Event()
        runs = []

        async def evaluation(room_id):
            runs.append(room_id)
            if room_id == ROOM:
                await release.wait()

        processor._process_batch_memberships_once = evaluation

        blocked = asyncio.ensure_future(processor.process_batch_memberships(ROOM))
        await asyncio.sleep(0)
        await processor.process_batch_memberships("!other:localhost")
        release.set()
        await blocked

        self.assertEqual(runs, [ROOM, "!other:localhost"])

    async def test_coalescing_is_shared_across_processor_instances(self):
        # The servlet constructs a fresh processor per request (and other
        # replicas construct their own); coalescing must apply across
        # instances via the shared claims store.
        claims = InMemoryClaims()
        first_processor, _ = make_processor(claims)
        second_processor, _ = make_processor(claims)
        release = asyncio.Event()
        runs = []

        async def evaluation(room_id):
            runs.append(room_id)
            if len(runs) == 1:
                await release.wait()

        first_processor._process_batch_memberships_once = evaluation
        second_processor._process_batch_memberships_once = evaluation

        first = asyncio.ensure_future(first_processor.process_batch_memberships(ROOM))
        await asyncio.sleep(0)
        await second_processor.process_batch_memberships(ROOM)
        release.set()
        await first

        self.assertEqual(runs, [ROOM, ROOM])

    async def test_rerun_requested_in_the_release_window_still_runs(self):
        # A request can coalesce in the instant between the holder's last
        # rerun check and its release. The release checks the flag in the
        # same statement it deletes, so that request must get its follow-up
        # run instead of being deleted unrun after its caller was already
        # told "coalesced".
        class RaceIntoReleaseWindow(InMemoryClaims):
            def __init__(self):
                super().__init__()
                self.raced = False

            async def release_if_no_rerun(self, room_id, token):
                if not self.raced:
                    self.raced = True
                    assert await self.request_rerun(room_id)
                return await super().release_if_no_rerun(room_id, token)

        claims = RaceIntoReleaseWindow()
        processor, _api = make_processor(claims)
        runs = []

        async def evaluation(room_id):
            runs.append(room_id)

        processor._process_batch_memberships_once = evaluation

        await processor.process_batch_memberships(ROOM)

        self.assertEqual(runs, [ROOM, ROOM])
        self.assertEqual(claims.rooms, {})

    async def test_claim_lost_at_release_time_stops_cleanly(self):
        # The claim expires and is taken over between the run finishing and
        # the release: neither the release nor a rerun consume can succeed
        # for the old token. The holder must stop (the new holder runs a
        # full evaluation) rather than loop or delete the new claim.
        class TakeoverAtRelease(InMemoryClaims):
            async def release_if_no_rerun(self, room_id, token):
                claim = self.rooms.get(room_id)
                if claim and claim["token"] == token:
                    claim["token"] = "new-holder"
                    claim["rerun"] = False
                return await super().release_if_no_rerun(room_id, token)

        claims = TakeoverAtRelease()
        processor, _api = make_processor(claims)
        runs = []

        async def evaluation(room_id):
            runs.append(room_id)

        processor._process_batch_memberships_once = evaluation

        await processor.process_batch_memberships(ROOM)

        self.assertEqual(runs, [ROOM])
        self.assertEqual(claims.rooms[ROOM]["token"], "new-holder")

    async def test_evaluation_failure_releases_the_room(self):
        processor, _api = make_processor()
        processor._process_batch_memberships_once = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        with self.assertRaises(RuntimeError):
            await processor.process_batch_memberships(ROOM)

        self.assertEqual(processor._claims.rooms, {})


class ClaimTestSuite(unittest.IsolatedAsyncioTestCase):
    """Cross-worker claim behaviors: expiry take-over, heartbeat, restore."""

    async def test_expired_claim_is_taken_over(self):
        claims = InMemoryClaims()
        # A worker crashed mid-evaluation and its claim has expired.
        claims.rooms[ROOM] = {"token": "dead-worker", "rerun": False, "expired": True}
        processor, _ = make_processor(claims)
        runs = []

        async def evaluation(room_id):
            runs.append(room_id)

        processor._process_batch_memberships_once = evaluation

        await processor.process_batch_memberships(ROOM)

        self.assertEqual(runs, [ROOM])
        self.assertEqual(claims.rooms, {})

    async def test_lost_claim_aborts_the_membership_loop(self):
        processor, _api = make_processor()
        users = [f"@u{i}:hs" for i in range(60)]
        stub_membership(processor, desired=users, invited=[], joined=[])

        async def heartbeat_lost(room_id, token, ttl_ms):
            return False

        processor._claims.heartbeat = heartbeat_lost

        with self.assertRaises(EvaluationClaimLost):
            await processor.process_batch_memberships(ROOM)

    async def test_restore_runs_under_the_claim(self):
        processor, api = make_processor()
        processor._desired_room_members = AsyncMock(return_value={"@a:hs"})

        await processor.restore_memberships_from_audience_criteria(ROOM)

        self.assertEqual(membership_changes(api), [("invite", "@a:hs")])
        self.assertEqual(processor._claims.rooms, {})

    async def test_restore_coalesces_into_inflight_evaluation(self):
        claims = InMemoryClaims()
        evaluating, restoring = make_processor(claims)[0], None
        release = asyncio.Event()
        runs = []

        async def evaluation(room_id):
            runs.append(("evaluation", room_id))
            if len(runs) == 1:
                await release.wait()

        evaluating._process_batch_memberships_once = evaluation

        restoring, api = make_processor(claims)
        restoring._desired_room_members = AsyncMock(return_value={"@a:hs"})
        restoring._process_batch_memberships_once = evaluation

        first = asyncio.ensure_future(evaluating.process_batch_memberships(ROOM))
        await asyncio.sleep(0)
        await restoring.restore_memberships_from_audience_criteria(ROOM)
        release.set()
        await first

        # The restore coalesced: no direct invites, one follow-up evaluation.
        api.update_room_membership.assert_not_awaited()
        self.assertEqual(runs, [("evaluation", ROOM), ("evaluation", ROOM)])
