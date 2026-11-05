"""Admits, runs, resumes and reports on fixtures deployments.

Every provisioning request is a deployment: a row in
``connect.fixture_deployments`` plus a background run on the main process.
The row is the deployment's durable record — its scenario, and a checkpoint of
how far it has got — so a deployment cut off by a restart (a redeploy, say)
picks up where it left off when the process comes back, rather than being
abandoned or torn down. A deployment is only ever deprovisioned because it
failed, or because a caller asked.

Guarantees, and how they hold:

- **No collisions.** All fixtures writes go through this runner. Admission and
  teardown pass through one ``AdmissionGate`` plus database checks: a Fixed
  name is reserved by one unfinished deployment at a time, and a run cannot
  be torn down while any of its deployments is unfinished. Several
  deployments may share a ``run_id``: each rolls back only what it created.
- **Survives restarts.** Every step is safe to repeat (see ``Provisioner``), so
  a resumed deployment re-runs whatever it had not checkpointed. Its deadline
  counts only time spent running.
- **Never stuck.** Each step has a timeout; a dependency that is briefly down
  is retried within a budget; the deployment stops between steps once
  cancelled or past its deadline.
- **No secrets at rest.** Credentials are minted when the caller collects
  them, returned once, and never stored.
"""

import logging
from dataclasses import dataclass
from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict
from synapse.util.stringutils import random_string

from connect.fixtures.checkpoint import Checkpoint
from connect.fixtures.gate import AdmissionGate
from connect.fixtures.journal import Journal
from connect.fixtures.provisioner import Deployment, Provisioner
from connect.fixtures.scenario_check import check_size
from connect.fixtures.store import DeploymentRecord, DeploymentStore
from connect.helpers.request_context import log_event

logger = logging.getLogger(__name__)

# How often a caller waiting on a deployment re-reads it.
WAIT_POLL_S = 0.1


@dataclass(frozen=True)
class RunnerLimits:
    """How large, how long and how many deployments may run."""

    max_resources: int = 100_000
    # Counted in running time only: downtime during a restart doesn't count.
    max_duration_s: float = 2 * 60 * 60
    max_concurrent: int = 4
    # The longest a POST may wait for its deployment before answering 202.
    wait_max_s: float = 60.0
    # How often a checkpoint is recorded while message-like steps finish (a
    # step that creates a user or room is always recorded at once).
    heartbeat_s: float = 5.0
    # A running deployment whose last checkpoint is older than this reads as
    # stalled.
    stalled_after_s: float = 120.0
    # Yield the reactor for `pace_s` after every `pace_every` steps, so a big
    # seed shares the main process with client traffic.
    pace_every: int = 100
    pace_s: float = 0.05


class DeploymentStoppedError(Exception):
    """A deployment stopped on purpose, between steps: cancelled or overdue."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


class DeploymentJournal(Journal):
    """Records a running deployment's checkpoints and stops it when asked."""

    def __init__(self, runner: "DeploymentRunner", deployment_id: str, clock):
        self._runner = runner
        self._deployment_id = deployment_id
        self._clock = clock
        self._limits = runner.limits
        self._last_tick_ms = clock.time_msec()
        self._last_saved_ms = self._last_tick_ms
        self._last_phase: str | None = None
        self._steps = 0

    async def before_step(self, step, checkpoint: Checkpoint) -> None:
        self._tick(checkpoint)
        if self._runner.is_cancelled(self._deployment_id):
            raise DeploymentStoppedError("cancelled", "deployment cancelled")
        if checkpoint.active_ms > self._limits.max_duration_s * 1000:
            raise DeploymentStoppedError(
                "failed",
                f"deployment ran longer than {self._limits.max_duration_s:g}s",
            )

    async def after_step(self, step, checkpoint: Checkpoint) -> None:
        self._tick(checkpoint)
        self._steps += 1
        heartbeat_due = (
            self._last_tick_ms - self._last_saved_ms >= self._limits.heartbeat_s * 1000
        )
        if step.is_structural or step.phase != self._last_phase or heartbeat_due:
            await self._runner.save_checkpoint(self._deployment_id, checkpoint)
            self._last_saved_ms = self._last_tick_ms
        self._last_phase = step.phase
        if self._limits.pace_every and self._steps % self._limits.pace_every == 0:
            await self._clock.sleep(self._limits.pace_s)

    def may_roll_back(self) -> bool:
        return not self._runner.shutting_down

    def _tick(self, checkpoint: Checkpoint) -> None:
        # Add the running time since the last tick. A resumed deployment starts
        # a new journal, so the downtime before it is never counted.
        now = self._clock.time_msec()
        checkpoint.active_ms += now - self._last_tick_ms
        self._last_tick_ms = now


class DeploymentRunner:
    """Admits deployments, runs them in the background, and reports on them.

    Lives on the main process, alongside the servlets that call it: every
    fixtures write happens there, which is what lets ``AdmissionGate`` close
    the window between checking for a conflict and recording a deployment.
    """

    def __init__(
        self,
        hs,
        api,
        provisioner: Provisioner,
        store: DeploymentStore | None = None,
        limits: RunnerLimits | None = None,
    ):
        self._api = api
        self._clock = hs.get_clock()
        self._provisioner = provisioner
        self._store = store or DeploymentStore(hs)
        self.limits = limits or RunnerLimits()
        self._gate = AdmissionGate()
        self._running: set[str] = set()
        self._cancelled: set[str] = set()
        self.shutting_down = False
        # A failure while the process stops is the shutdown's doing: the
        # deployment must resume afterwards, not roll back.
        hs.get_reactor().addSystemEventTrigger("before", "shutdown", self._on_shutdown)

    async def resume(self) -> None:
        """Pick up every deployment left unfinished by the last process.

        Called once, shortly after start-up. Deployments that were running
        resume from their checkpoints; queued ones start as capacity allows.
        """
        unfinished = await self._store.unfinished()
        for record in unfinished:
            if record.status != "queued":
                if record.cancel_requested:
                    self._cancelled.add(record.id)
                log_event(
                    logger,
                    "Resuming fixtures deployment",
                    deployment_id=record.id,
                    run_id=record.run_id,
                    status=record.status,
                )
                self._launch(record.id)
        await self._launch_queued()

    async def admit(self, scenario: JsonDict, key: str | None) -> DeploymentRecord:
        """Check ``scenario`` and record it as a new deployment, or find ``key``'s.

        Args:
            scenario (JsonDict): The scenario request (a resource DAG).
            key (str | None): The caller's idempotency key. Re-sending it returns the deployment it first admitted.

        Returns:
            DeploymentRecord: The admitted (or previously admitted) deployment.

        Raises:
            SynapseError: 400 for a bad scenario, 413 if it is too large, 409 if it conflicts with an unfinished deployment or a teardown.
        """  # noqa: DAR401, DAR402
        if key is not None:
            existing = await self._store.get_by_key(key)
            if existing is not None:
                return existing
        check_size(scenario, self.limits.max_resources)
        self._provisioner.validate(scenario)

        run_id = scenario["run_id"]
        fixed = self._fixed_localparts(scenario)
        self._gate.begin_admission(run_id, key, fixed)
        try:
            if key is not None and await self._store.get_by_key(key) is not None:
                raise self._conflict(f"a deployment with key '{key}' already exists")
            reserved = {
                localpart
                for record in await self._store.unfinished()
                for localpart in record.fixed_localparts
            }
            taken = reserved.intersection(fixed)
            if taken:
                raise self._conflict(
                    f"Fixed user {sorted(taken)[0]} is reserved by an unfinished "
                    "deployment"
                )
            deployment_id = random_string(16)
            await self._store.insert(
                deployment_id,
                key,
                run_id,
                scenario,
                fixed,
                self._clock.time_msec(),
            )
        finally:
            self._gate.end_admission(run_id, key, fixed)

        log_event(
            logger,
            "Admitted fixtures deployment",
            deployment_id=deployment_id,
            run_id=run_id,
            phases=Provisioner.phases(scenario),
        )
        await self._launch_queued()
        return await self._store.get(deployment_id)

    async def get(self, deployment_id: str) -> DeploymentRecord | None:
        """The deployment ``deployment_id``, or ``None``.

        Args:
            deployment_id (str): The deployment's id.

        Returns:
            DeploymentRecord | None: Its record, if it exists.
        """
        return await self._store.get(deployment_id)

    async def wait(self, deployment_id: str, wait_s: float) -> DeploymentRecord:
        """Wait up to ``wait_s`` (capped) for a deployment to finish.

        Args:
            deployment_id (str): The deployment's id.
            wait_s (float): How long the caller is prepared to wait.

        Returns:
            DeploymentRecord: Its record, finished or not.
        """
        deadline = self._clock.time_msec() + min(wait_s, self.limits.wait_max_s) * 1000
        while True:
            record = await self._store.get(deployment_id)
            if not record.is_unfinished or self._clock.time_msec() >= deadline:
                return record
            await self._clock.sleep(WAIT_POLL_S)

    async def report(self, record: DeploymentRecord, collect: bool) -> JsonDict:
        """Where ``record`` stands — and, once complete, what it provisioned.

        Args:
            record (DeploymentRecord): The deployment.
            collect (bool): Whether to hand out its credentials, if no caller has collected them yet. They are minted now and never stored.

        Returns:
            JsonDict: Status, progress, error, and the graph once complete.
        """
        report = self._progress(record)
        if record.status != "complete":
            return report

        checkpoint = Checkpoint.from_json(record.checkpoint)
        deployment = self._deployment(record)
        graph = self._provisioner.graph(deployment, checkpoint)
        claimed = record.credentials_claimed
        if collect and not claimed and await self._store.claim_credentials(record.id):
            self._merge_credentials(
                graph,
                await self._provisioner.issue_credentials(record.scenario, checkpoint),
            )
            claimed = True
        return {**report, **graph, "credentials_claimed": claimed}

    async def reissue_credentials(self, deployment_id: str) -> JsonDict | None:
        """Mint fresh credentials for a complete deployment's users.

        Args:
            deployment_id (str): The deployment's id.

        Returns:
            JsonDict | None: The report with new credentials, or ``None`` if there is no such deployment.

        Raises:
            SynapseError: 409 if the deployment is not complete.
        """  # noqa: DAR401, DAR402
        record = await self._store.get(deployment_id)
        if record is None:
            return None
        if record.status != "complete":
            raise self._conflict(f"deployment is {record.status}, not complete")
        await self._store.claim_credentials(record.id)
        checkpoint = Checkpoint.from_json(record.checkpoint)
        graph = self._provisioner.graph(self._deployment(record), checkpoint)
        self._merge_credentials(
            graph,
            await self._provisioner.issue_credentials(record.scenario, checkpoint),
        )
        return {**self._progress(record), **graph, "credentials_claimed": True}

    async def delete(self, deployment_id: str) -> JsonDict | None:
        """Cancel an unfinished deployment, or deprovision a complete one.

        Either way only this deployment's own resources are removed. Both
        happen in the background; poll the deployment to see them finish.

        Args:
            deployment_id (str): The deployment's id.

        Returns:
            JsonDict | None: Its report, or ``None`` if there is no such deployment.
        """
        record = await self._store.get(deployment_id)
        if record is None:
            return None
        if record.status in ("queued", "running"):
            await self._store.request_cancel(record.id)
            self._cancelled.add(record.id)
            log_event(
                logger,
                "Cancel requested for fixtures deployment",
                deployment_id=record.id,
                run_id=record.run_id,
            )
            if record.status == "queued" and record.id not in self._running:
                self._launch(record.id)
        elif record.status == "complete":
            await self._store.set_status(
                record.id, "deprovisioning", now_ms=self._clock.time_msec()
            )
            self._launch(record.id)
        return await self.report(await self._store.get(record.id), collect=False)

    async def teardown(self, run_id: str | None) -> JsonDict:
        """Tear down a run (``None``: every run), once nothing of it is running.

        Args:
            run_id (str | None): The run to tear down, or ``None`` for all.

        Returns:
            JsonDict: ``deactivated`` (users) and ``rooms_purged`` counts.

        Raises:
            SynapseError: 409 while a deployment of the run (or any, for all) is unfinished.
        """  # noqa: DAR401, DAR402
        self._gate.begin_teardown(run_id)
        try:
            unfinished = await self._store.unfinished(run_id)
            if unfinished:
                record = unfinished[0]
                raise self._conflict(
                    f"deployment {record.id} of run '{record.run_id}' is still "
                    f"{record.status}; wait for it, or DELETE it first"
                )
            if run_id is None:
                result = await self._provisioner.cleanup_all()
            else:
                result = await self._provisioner.cleanup_run(run_id)
            await self._store.mark_deprovisioned(run_id, self._clock.time_msec())
            return result
        finally:
            self._gate.end_teardown(run_id)

    def is_cancelled(self, deployment_id: str) -> bool:
        """Whether ``deployment_id`` has been asked to stop.

        Args:
            deployment_id (str): The deployment's id.

        Returns:
            bool: True once a cancel has been requested.
        """
        return deployment_id in self._cancelled

    async def save_checkpoint(self, deployment_id: str, checkpoint: Checkpoint) -> None:
        """Record ``checkpoint`` as ``deployment_id``'s progress.

        Args:
            deployment_id (str): The deployment's id.
            checkpoint (Checkpoint): Its progress.
        """
        await self._store.save_checkpoint(
            deployment_id, checkpoint.to_json(), self._clock.time_msec()
        )

    def _launch(self, deployment_id: str) -> None:
        # Claimed synchronously, so one deployment is never run twice at once.
        if deployment_id in self._running:
            return
        self._running.add(deployment_id)
        self._api.run_as_background_process(
            "fixtures_deployment", self._execute, deployment_id
        )

    async def _launch_queued(self) -> None:
        free = self.limits.max_concurrent - len(self._running)
        if free <= 0:
            return
        queued = [
            record
            for record in await self._store.unfinished()
            if record.status == "queued" and record.id not in self._running
        ]
        for record in queued[:free]:
            self._launch(record.id)

    async def _execute(self, deployment_id: str) -> None:
        try:
            record = await self._store.get(deployment_id)
            if record.status == "deprovisioning":
                await self._deprovision(record)
            else:
                await self._provision(record)
        finally:
            self._running.discard(deployment_id)
            if not self.shutting_down:
                self._cancelled.discard(deployment_id)
                await self._launch_queued()

    async def _provision(self, record: DeploymentRecord) -> None:
        await self._store.set_status(
            record.id, "running", now_ms=self._clock.time_msec()
        )
        try:
            checkpoint = Checkpoint.from_json(record.checkpoint)
            journal = DeploymentJournal(self, record.id, self._clock)
            await self._provisioner.execute(
                self._deployment(record), checkpoint, journal
            )
            await self.save_checkpoint(record.id, checkpoint)
        except DeploymentStoppedError as stopped:
            if not self.shutting_down:
                await self._finish(record, stopped.status, error=stopped)
            return
        except Exception as error:
            if self.shutting_down:
                logger.info(
                    "Fixtures deployment %s interrupted by shutdown; it resumes "
                    "on start-up",
                    record.id,
                )
                return
            # logger.exception reaches Sentry.
            logger.exception(
                "Fixtures deployment %s (run %s) failed and rolled back",
                record.id,
                record.run_id,
            )
            await self._finish(record, "failed", error=error)
            return
        await self._finish(record, "complete")

    async def _deprovision(self, record: DeploymentRecord) -> None:
        try:
            result = await self._provisioner.deprovision(
                Checkpoint.from_json(record.checkpoint)
            )
        except Exception as error:
            if self.shutting_down:
                return
            logger.exception("Deprovisioning fixtures deployment %s failed", record.id)
            await self._finish(record, "complete", error=error)
            return
        await self._finish(record, "deprovisioned", **result)

    async def _finish(
        self,
        record: DeploymentRecord,
        status: str,
        error: Exception | None = None,
        **fields,
    ) -> None:
        code, errcode = HTTPStatus.INTERNAL_SERVER_ERROR, Codes.UNKNOWN
        message = str(error) if error else None
        if isinstance(error, SynapseError):
            code, errcode, message = error.code, error.errcode, error.msg
        await self._store.set_status(
            record.id,
            status,
            now_ms=self._clock.time_msec(),
            error=message,
            error_code=int(code) if error else None,
            errcode=errcode if error else None,
        )
        log_event(
            logger,
            f"Fixtures deployment {status}",
            level=logging.WARNING if error else logging.INFO,
            deployment_id=record.id,
            run_id=record.run_id,
            error=message,
            **fields,
        )

    def _progress(self, record: DeploymentRecord) -> JsonDict:
        phases = Provisioner.phases(record.scenario)
        next_index = (record.checkpoint or {}).get("next_index", 0)
        if record.status in ("complete", "deprovisioning", "deprovisioned"):
            next_index = sum(phases.values())
        done_by_phase = {}
        start = 0
        for name, total in phases.items():
            done_by_phase[name] = {
                "done": min(max(next_index - start, 0), total),
                "total": total,
            }
            start += total
        current = next(
            (name for name, p in done_by_phase.items() if p["done"] < p["total"]), None
        )
        total = sum(phases.values())
        heartbeat_ms = record.heartbeat_ms
        stalled = (
            record.status == "running"
            and heartbeat_ms is not None
            and self._clock.time_msec() - heartbeat_ms
            > self.limits.stalled_after_s * 1000
        )
        return {
            "deployment_id": record.id,
            "key": record.key,
            "run_id": record.run_id,
            "status": record.status,
            "phase": current if record.is_unfinished else None,
            "phases": done_by_phase,
            "percent": round(100 * next_index / total, 1) if total else 100.0,
            "created_at": record.created_ms,
            "heartbeat_at": heartbeat_ms,
            "stalled": stalled,
            "cancel_requested": record.cancel_requested or record.id in self._cancelled,
            "error": record.error,
            "errcode": record.errcode,
        }

    @staticmethod
    def _merge_credentials(graph: JsonDict, credentials: dict[str, JsonDict]) -> None:
        for ref, issued in credentials.items():
            graph["users"][ref].update(issued)

    @staticmethod
    def _deployment(record: DeploymentRecord) -> Deployment:
        return Deployment(
            id=record.id,
            run_id=record.run_id,
            scenario=record.scenario,
            admitted_ms=record.created_ms,
        )

    @staticmethod
    def _fixed_localparts(scenario: JsonDict) -> list[str]:
        return [
            localpart
            for spec in scenario.get("users", [])
            if (localpart := (spec.get("identity") or {}).get("localpart"))
        ]

    @staticmethod
    def _conflict(message: str) -> SynapseError:
        return SynapseError(HTTPStatus.CONFLICT, message, Codes.UNKNOWN)

    def _on_shutdown(self) -> None:
        self.shutting_down = True
