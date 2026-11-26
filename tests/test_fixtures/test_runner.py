"""Tests for the deployment runner: admission, running, resuming, reporting."""

import unittest
from dataclasses import replace
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import SynapseError

from fixtures.checkpoint import Checkpoint
from fixtures.provisioner import Provisioner, Step
from fixtures.runner import (
    DeploymentJournal,
    DeploymentRunner,
    DeploymentStoppedError,
    RunnerLimits,
)
from fixtures.store import UNFINISHED, DeploymentRecord


class FakeStore:
    """An in-memory ``connect.fixture_deployments``."""

    def __init__(self):
        self.rows = {}

    async def insert(self, deployment_id, key, run_id, scenario, fixed, created_ms):
        self.rows[deployment_id] = DeploymentRecord(
            id=deployment_id,
            key=key,
            run_id=run_id,
            status="queued",
            cancel_requested=False,
            credentials_claimed=False,
            scenario=scenario,
            checkpoint=None,
            fixed_localparts=fixed,
            error=None,
            error_code=None,
            errcode=None,
            created_ms=created_ms,
            heartbeat_ms=None,
            finished_ms=None,
        )

    async def get(self, deployment_id):
        return self.rows.get(deployment_id)

    async def get_by_key(self, key):
        return next((r for r in self.rows.values() if r.key == key), None)

    async def unfinished(self, run_id=None):
        return [
            r
            for r in self.rows.values()
            if r.status in UNFINISHED and (run_id is None or r.run_id == run_id)
        ]

    async def save_checkpoint(self, deployment_id, checkpoint, heartbeat_ms):
        self._set(deployment_id, checkpoint=checkpoint, heartbeat_ms=heartbeat_ms)

    async def set_status(self, deployment_id, status, *, now_ms, **error):
        self._set(deployment_id, status=status, heartbeat_ms=now_ms, **error)

    async def request_cancel(self, deployment_id):
        self._set(deployment_id, cancel_requested=True)

    async def claim_credentials(self, deployment_id):
        if self.rows[deployment_id].credentials_claimed:
            return False
        self._set(deployment_id, credentials_claimed=True)
        return True

    async def mark_deprovisioned(self, run_id, now_ms):
        for row in list(self.rows.values()):
            if row.status == "complete" and run_id in (None, row.run_id):
                self._set(row.id, status="deprovisioned")

    def _set(self, deployment_id, **changes):
        self.rows[deployment_id] = replace(self.rows[deployment_id], **changes)


class FakeApi:
    """Collects background runs so a test decides when they happen."""

    def __init__(self):
        self.pending = []

    def run_as_background_process(self, desc, func, *args):
        self.pending.append((func, args))

    async def drain(self):
        while self.pending:
            func, args = self.pending.pop(0)
            await func(*args)


class FakeClock:
    def __init__(self):
        self.now = 1_000_000

    def time_msec(self):
        return self.now

    async def sleep(self, seconds):
        self.now += int(seconds * 1000)


def make_runner(limits=None):
    hs = MagicMock()
    clock = FakeClock()
    hs.get_clock.return_value = clock
    store = FakeStore()
    api = FakeApi()
    provisioner = MagicMock()
    provisioner.validate = MagicMock()
    provisioner.execute = AsyncMock(side_effect=lambda d, checkpoint, j: checkpoint)
    provisioner.graph = MagicMock(
        side_effect=lambda d, c: {
            "run_id": d.run_id,
            "users": {"alice": {"mxid": "@alice:hs"}},
            "rooms": {},
            "events": {},
        }
    )
    provisioner.issue_credentials = AsyncMock(
        return_value={"alice": {"password": "s3cret-pw"}}
    )
    provisioner.deprovision = AsyncMock(
        return_value={"deactivated": 1, "rooms_purged": 0}
    )
    provisioner.cleanup_run = AsyncMock(
        return_value={"deactivated": 0, "rooms_purged": 0}
    )
    provisioner.cleanup_all = AsyncMock(
        return_value={"deactivated": 0, "rooms_purged": 0}
    )
    runner = DeploymentRunner(hs, api, provisioner, store=store, limits=limits)
    return runner, store, api, provisioner, clock


def scenario(run_id="run-a", **extra):
    return {"run_id": run_id, "users": [{"ref": "alice"}], **extra}


def fixed_user_scenario(run_id="run-a"):
    return scenario(
        run_id, users=[{"ref": "a", "identity": {"localpart": "bob.smith"}}]
    )


class AdmitTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_records_a_queued_deployment_and_starts_it(self):
        runner, store, api, _, _ = make_runner()

        record = await runner.admit(scenario(), None)

        self.assertEqual(record.status, "queued")
        self.assertEqual(store.rows[record.id].scenario, scenario())
        self.assertEqual(len(api.pending), 1)

    async def test_resending_a_key_returns_the_first_deployment(self):
        runner, store, _, _, _ = make_runner()
        first = await runner.admit(scenario(), "k1")

        again = await runner.admit(scenario(), "k1")

        self.assertEqual(again.id, first.id)
        self.assertEqual(len(store.rows), 1)

    async def test_a_bad_scenario_is_refused_before_anything_is_recorded(self):
        runner, store, _, provisioner, _ = make_runner()
        provisioner.validate.side_effect = SynapseError(HTTPStatus.BAD_REQUEST, "bad")

        with self.assertRaises(SynapseError):
            await runner.admit(scenario(), None)

        self.assertEqual(store.rows, {})

    async def test_a_scenario_over_the_size_limit_is_refused(self):
        runner, _, _, _, _ = make_runner(RunnerLimits(max_resources=0))

        with self.assertRaises(SynapseError) as ctx:
            await runner.admit(scenario(), None)

        self.assertEqual(ctx.exception.code, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    async def test_a_fixed_name_reserved_by_an_unfinished_deployment_conflicts(self):
        runner, _, _, _, _ = make_runner()
        await runner.admit(fixed_user_scenario("run-a"), None)

        with self.assertRaises(SynapseError) as ctx:
            await runner.admit(fixed_user_scenario("run-b"), None)

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
        self.assertIn("bob.smith", ctx.exception.msg)

    async def test_a_fixed_name_is_free_again_once_its_deployment_finished(self):
        runner, _, api, _, _ = make_runner()
        await runner.admit(fixed_user_scenario("run-a"), None)
        await api.drain()

        record = await runner.admit(fixed_user_scenario("run-b"), None)

        self.assertEqual(record.status, "queued")

    async def test_deployments_may_share_a_run_id(self):
        runner, store, _, _, _ = make_runner()

        await runner.admit(scenario("run-a"), None)
        await runner.admit(scenario("run-a"), None)

        self.assertEqual(len(store.rows), 2)

    async def test_a_run_being_torn_down_admits_nothing(self):
        runner, _, _, _, _ = make_runner()
        runner._gate.begin_teardown("run-a")

        with self.assertRaises(SynapseError) as ctx:
            await runner.admit(scenario("run-a"), None)

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)

    async def test_runs_at_most_max_concurrent_at_once(self):
        runner, _, api, _, _ = make_runner(RunnerLimits(max_concurrent=1))

        await runner.admit(scenario(), None)
        await runner.admit(scenario(), None)

        self.assertEqual(len(api.pending), 1)


class RunTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_a_deployment_runs_to_complete(self):
        runner, store, api, provisioner, _ = make_runner()
        record = await runner.admit(scenario(), None)

        await api.drain()

        self.assertEqual(store.rows[record.id].status, "complete")
        self.assertIsNotNone(store.rows[record.id].checkpoint)
        provisioner.execute.assert_awaited_once()

    async def test_the_next_queued_deployment_starts_when_one_finishes(self):
        runner, store, api, _, _ = make_runner(RunnerLimits(max_concurrent=1))
        first = await runner.admit(scenario(), None)
        second = await runner.admit(scenario(), None)

        await api.drain()

        self.assertEqual(store.rows[first.id].status, "complete")
        self.assertEqual(store.rows[second.id].status, "complete")

    async def test_a_failure_is_recorded_with_its_error(self):
        runner, store, api, provisioner, _ = make_runner()
        provisioner.execute.side_effect = SynapseError(
            HTTPStatus.CONFLICT, "name taken", "M_USER_IN_USE"
        )
        record = await runner.admit(scenario(), None)

        with self.assertLogs(level="ERROR"):
            await api.drain()

        row = store.rows[record.id]
        self.assertEqual(row.status, "failed")
        self.assertEqual(row.error, "name taken")
        self.assertEqual(row.error_code, HTTPStatus.CONFLICT)
        self.assertEqual(row.errcode, "M_USER_IN_USE")

    async def test_a_stop_is_recorded_as_its_status(self):
        runner, store, api, provisioner, _ = make_runner()
        provisioner.execute.side_effect = DeploymentStoppedError(
            "cancelled", "deployment cancelled"
        )
        record = await runner.admit(scenario(), None)

        await api.drain()

        self.assertEqual(store.rows[record.id].status, "cancelled")

    async def test_a_failure_during_shutdown_leaves_the_deployment_to_resume(self):
        runner, store, api, provisioner, _ = make_runner()
        provisioner.execute.side_effect = Exception("db pool closed")
        record = await runner.admit(scenario(), None)
        runner._on_shutdown()

        await api.drain()

        self.assertEqual(store.rows[record.id].status, "running")

    async def test_resume_restarts_running_deployments_and_honours_cancels(self):
        runner, store, api, _, _ = make_runner()
        running = await runner.admit(scenario(), None)
        api.pending.clear()
        store._set(running.id, status="running", cancel_requested=True)
        restarted, _, restarted_api, _, _ = make_runner()
        restarted._store = store

        await restarted.resume()

        self.assertEqual(len(restarted_api.pending), 1)
        self.assertTrue(restarted.is_cancelled(running.id))


class ReportTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_credentials_are_handed_out_once(self):
        runner, store, api, _, _ = make_runner()
        record = await runner.admit(scenario(), None)
        await api.drain()

        first = await runner.report(store.rows[record.id], collect=True)
        second = await runner.report(store.rows[record.id], collect=True)

        self.assertEqual(first["users"]["alice"]["password"], "s3cret-pw")
        self.assertTrue(first["credentials_claimed"])
        self.assertNotIn("password", second["users"]["alice"])

    async def test_credentials_are_never_stored(self):
        runner, store, api, _, _ = make_runner()
        record = await runner.admit(scenario(), None)
        await api.drain()

        await runner.report(store.rows[record.id], collect=True)

        self.assertNotIn("s3cret-pw", repr(store.rows[record.id]))

    async def test_fresh_credentials_can_be_reissued(self):
        runner, store, api, provisioner, _ = make_runner()
        record = await runner.admit(scenario(), None)
        await api.drain()
        await runner.report(store.rows[record.id], collect=True)

        reissued = await runner.reissue_credentials(record.id)

        self.assertEqual(reissued["users"]["alice"]["password"], "s3cret-pw")
        self.assertEqual(provisioner.issue_credentials.await_count, 2)

    async def test_reports_progress_by_phase(self):
        runner, store, _, _, _ = make_runner()
        record = await runner.admit(
            scenario(events=[{"room": "r", "sender": "alice"}] * 4), None
        )
        store._set(record.id, status="running", checkpoint={"next_index": 3})

        report = await runner.report(store.rows[record.id], collect=True)

        self.assertEqual(report["phases"]["users"], {"done": 1, "total": 1})
        self.assertEqual(report["phases"]["events"], {"done": 2, "total": 4})
        self.assertEqual(report["phase"], "events")
        self.assertEqual(report["percent"], 60.0)

    async def test_a_running_deployment_without_a_recent_checkpoint_is_stalled(self):
        runner, store, _, _, clock = make_runner()
        record = await runner.admit(scenario(), None)
        store._set(record.id, status="running", heartbeat_ms=clock.now)
        clock.now += 121_000

        report = await runner.report(store.rows[record.id], collect=False)

        self.assertTrue(report["stalled"])


class WaitTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_returns_once_the_deployment_finishes(self):
        runner, store, _, _, _ = make_runner()
        record = await runner.admit(scenario(), None)
        store._set(record.id, status="complete")

        result = await runner.wait(record.id, 30)

        self.assertEqual(result.status, "complete")

    async def test_gives_up_after_the_capped_wait(self):
        runner, _, _, _, clock = make_runner(RunnerLimits(wait_max_s=2))
        record = await runner.admit(scenario(), None)
        started = clock.now

        result = await runner.wait(record.id, 600)

        self.assertEqual(result.status, "queued")
        self.assertLess(clock.now - started, 2_500)


class DeleteTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_deleting_a_queued_deployment_cancels_it(self):
        runner, store, api, provisioner, _ = make_runner()
        provisioner.execute.side_effect = DeploymentStoppedError(
            "cancelled", "deployment cancelled"
        )
        record = await runner.admit(scenario(), None)

        report = await runner.delete(record.id)
        await api.drain()

        self.assertTrue(report["cancel_requested"])
        self.assertTrue(store.rows[record.id].cancel_requested)
        self.assertEqual(store.rows[record.id].status, "cancelled")

    async def test_deleting_a_complete_deployment_deprovisions_it(self):
        runner, store, api, provisioner, _ = make_runner()
        record = await runner.admit(scenario(), None)
        await api.drain()

        report = await runner.delete(record.id)
        await api.drain()

        self.assertEqual(report["status"], "deprovisioning")
        self.assertEqual(store.rows[record.id].status, "deprovisioned")
        provisioner.deprovision.assert_awaited_once()

    async def test_an_unknown_deployment_is_none(self):
        runner, _, _, _, _ = make_runner()

        self.assertIsNone(await runner.delete("nope"))


class TeardownTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_while_a_deployment_of_the_run_is_unfinished(self):
        runner, _, _, provisioner, _ = make_runner()
        await runner.admit(scenario("run-a"), None)

        with self.assertRaises(SynapseError) as ctx:
            await runner.teardown("run-a")

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
        provisioner.cleanup_run.assert_not_awaited()

    async def test_another_runs_deployment_does_not_block_it(self):
        runner, _, _, provisioner, _ = make_runner()
        await runner.admit(scenario("run-b"), None)

        await runner.teardown("run-a")

        provisioner.cleanup_run.assert_awaited_once_with("run-a")

    async def test_global_teardown_refuses_while_any_deployment_is_unfinished(self):
        runner, _, _, provisioner, _ = make_runner()
        await runner.admit(scenario("run-b"), None)

        with self.assertRaises(SynapseError):
            await runner.teardown(None)

        provisioner.cleanup_all.assert_not_awaited()

    async def test_marks_the_runs_complete_deployments_deprovisioned(self):
        runner, store, api, _, _ = make_runner()
        record = await runner.admit(scenario("run-a"), None)
        await api.drain()

        await runner.teardown("run-a")

        self.assertEqual(store.rows[record.id].status, "deprovisioned")


def step(index, kind="event"):
    return Step(index, "events", kind, {})


class JournalTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_counts_only_running_time_towards_the_deadline(self):
        runner, _, _, _, clock = make_runner(RunnerLimits(max_duration_s=10))
        checkpoint = Checkpoint(active_ms=9_000)
        # Downtime before a resume: the new journal starts its clock now.
        clock.now += 3_600_000
        journal = DeploymentJournal(runner, "dep", clock)

        await journal.before_step(step(0), checkpoint)

        self.assertEqual(checkpoint.active_ms, 9_000)

    async def test_stops_a_deployment_past_its_deadline(self):
        runner, _, _, _, clock = make_runner(RunnerLimits(max_duration_s=10))
        journal = DeploymentJournal(runner, "dep", clock)
        clock.now += 11_000

        with self.assertRaises(DeploymentStoppedError) as ctx:
            await journal.before_step(step(0), Checkpoint())

        self.assertEqual(ctx.exception.status, "failed")

    async def test_stops_a_cancelled_deployment(self):
        runner, _, _, _, clock = make_runner()
        runner._cancelled.add("dep")
        journal = DeploymentJournal(runner, "dep", clock)

        with self.assertRaises(DeploymentStoppedError) as ctx:
            await journal.before_step(step(0), Checkpoint())

        self.assertEqual(ctx.exception.status, "cancelled")

    async def test_saves_a_structural_step_at_once_and_others_on_the_heartbeat(self):
        runner, _, _, _, clock = make_runner(RunnerLimits(pace_every=0))
        runner.save_checkpoint = AsyncMock()
        journal = DeploymentJournal(runner, "dep", clock)
        checkpoint = Checkpoint()

        await journal.after_step(Step(0, "users", "user", {}), checkpoint)
        await journal.after_step(step(1), checkpoint)
        await journal.after_step(step(2), checkpoint)
        clock.now += 5_000
        await journal.after_step(step(3), checkpoint)

        saved = [call.args[0] for call in runner.save_checkpoint.await_args_list]
        # The user step; the first event step (a new phase); the heartbeat.
        self.assertEqual(len(saved), 3)

    async def test_never_rolls_back_while_shutting_down(self):
        runner, _, _, _, clock = make_runner()
        journal = DeploymentJournal(runner, "dep", clock)

        runner._on_shutdown()

        self.assertFalse(journal.may_roll_back())


class PhasesTestSuite(unittest.TestCase):
    def test_report_phases_match_the_provisioner_plan(self):
        self.assertEqual(
            list(Provisioner.phases(scenario())),
            [
                "users",
                "rooms",
                "events",
                "media",
                "reactions",
                "redactions",
                "receipts",
                "audiences",
                "departures",
            ],
        )


if __name__ == "__main__":
    unittest.main()
