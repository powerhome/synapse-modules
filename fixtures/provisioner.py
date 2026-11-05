"""Orchestrates a provisioning scenario across its resource types."""

import hashlib
import logging
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict, UserID

from connect.fixtures.archive import RoomArchiver
from connect.fixtures.audience import AudienceProvisioner
from connect.fixtures.auth import authenticate_fixtures_request
from connect.fixtures.checkpoint import Checkpoint
from connect.fixtures.cleanup import Cleanup, RoomCleanup
from connect.fixtures.directory import DirectoryEnricher
from connect.fixtures.events import EventProvisioner
from connect.fixtures.journal import Journal
from connect.fixtures.markers import RUN_MARKER, deployment_from_marker
from connect.fixtures.media import MediaProvisioner
from connect.fixtures.membership import MembershipProvisioner
from connect.fixtures.profiles import ProfileWriter
from connect.fixtures.reactions import ReactionProvisioner
from connect.fixtures.receipts import ReceiptProvisioner
from connect.fixtures.redactions import RedactionProvisioner
from connect.fixtures.refs import resolve_user_ref
from connect.fixtures.retry import Backoff, is_transient
from connect.fixtures.rooms import RoomProvisioner
from connect.fixtures.scenario_check import check_refs
from connect.fixtures.steps import Steps
from connect.fixtures.users import UserProvisioner

logger = logging.getLogger(__name__)

# The phases a scenario runs, in order.
PHASES = (
    "users",
    "rooms",
    "events",
    "media",
    "reactions",
    "redactions",
    "receipts",
    "audiences",
    "departures",
)

# Steps that create a user or a room. The rest of the scenario refers to what
# they make, so the runner records a checkpoint after every one of them.
STRUCTURAL_KINDS = ("user", "room")


@dataclass(frozen=True)
class Step:
    """One unit of a scenario: create one resource, or apply one change."""

    index: int
    phase: str
    kind: str
    spec: JsonDict

    @property
    def is_structural(self) -> bool:
        """Whether later steps depend on what this step creates.

        Returns:
            bool: True for a step that creates a user or a room.
        """
        return self.kind in STRUCTURAL_KINDS


@dataclass(frozen=True)
class Deployment:
    """What the provisioner needs to know about the deployment it is running."""

    id: str
    run_id: str
    scenario: JsonDict
    admitted_ms: int


@dataclass
class _Run:
    # Per-execution context: what a resumed deployment re-derives on start.
    deployment: Deployment
    run_prefix: str
    deactivated_refs: set
    # The index of the first step this execution runs. When resuming, that
    # step may have been cut off part-way by the restart.
    first_index: int
    resumed: bool
    audiences_validated: bool = field(default=False)

    def txn_id(self, step: Step) -> str:
        return f"fx:{self.deployment.id}:{step.index}"


class Provisioner:
    """Resolves a scenario (a small resource DAG) into provisioned fixtures.

    Resolution order is users -> rooms -> memberships -> events -> media ->
    reactions -> redactions -> receipts -> audiences (room contexts) ->
    departures (``left`` members, ``deactivated`` users, then ``archived`` rooms), so later resources can
    reference earlier ones by their scenario-local ``ref``.

    A scenario runs as a flat list of steps (``plan``). Every step is safe to
    run again, so a deployment cut off by a restart resumes from its last
    checkpoint and simply re-runs whatever it had not yet recorded: a user or
    room it had made is found and adopted, and a message it had sent is
    returned by Synapse's transaction-id deduplication instead of being sent
    twice.
    """

    def __init__(
        self,
        hs,
        shared_secret: str,
        idp_id=None,
        audiences_base_url=None,
        bot_user_ids=None,
        media_repo_token=None,
        connect_access_group_external_id="",
        steps: Steps | None = None,
        retry_budget_s: float = 600.0,
    ):
        self.hs = hs
        self.store = hs.get_datastores().main
        self.clock = hs.get_clock()
        self._secret = shared_secret
        # Each provisioning call runs through `steps`, which may bound it with a
        # timeout so one hung call fails the scenario instead of stalling it.
        self.steps = steps or Steps()
        # How long one step may keep retrying a dependency that is down.
        self._retry_budget_s = retry_budget_s

        directory = (
            DirectoryEnricher(hs, audiences_base_url, connect_access_group_external_id)
            if audiences_base_url
            else None
        )
        profiles = ProfileWriter(hs)
        self.users = UserProvisioner(
            hs, idp_id=idp_id, directory=directory, profiles=profiles
        )
        self.rooms = RoomProvisioner(hs)
        self.members = MembershipProvisioner(hs)
        self.events = EventProvisioner(hs)
        self.reactions = ReactionProvisioner(hs)
        self.redactions = RedactionProvisioner(hs)
        self.audience = AudienceProvisioner(hs, audiences_base_url)
        self.archive = RoomArchiver(hs, bot_user_ids or [])
        self.receipts = ReceiptProvisioner(hs, shared_secret)
        self.media = MediaProvisioner(hs, media_repo_token or "")
        self.cleanup = Cleanup(hs, profiles=profiles)
        self.room_cleanup = RoomCleanup(hs)

    def authenticate(self, request: SynapseRequest) -> None:
        # Reject the request unless it carries the shared secret.
        authenticate_fixtures_request(request, self._secret)

    @staticmethod
    def run_prefix(run_id: str) -> str:
        """The localpart prefix that tags every user created for ``run_id``.

        Deterministic in ``run_id`` so cleanup can find a run's users by prefix
        without a tracking table — and so an orphaned run (one whose caller
        crashed before teardown) is still cleanable by its id.

        Args:
            run_id (str): The caller-supplied id tagging a provisioning run.

        Returns:
            str: The localpart prefix shared by every user in the run.
        """
        # Not security-sensitive: just a short, stable tag derived from run_id.
        digest = hashlib.sha1(run_id.encode("utf-8"), usedforsecurity=False)
        return f"fx_{digest.hexdigest()[:10]}_"

    @staticmethod
    def plan(scenario: JsonDict) -> list[Step]:
        """The steps ``scenario`` runs, in order.

        Args:
            scenario (JsonDict): The scenario request (a resource DAG).

        Returns:
            list[Step]: Every step, indexed from 0.
        """
        rooms = scenario.get("rooms", [])
        users = scenario.get("users", [])
        kinds = [
            ("users", "user", users),
            ("rooms", "room", rooms),
            ("events", "event", scenario.get("events", [])),
            ("media", "media", scenario.get("media", [])),
            ("reactions", "reaction", scenario.get("reactions", [])),
            ("redactions", "redaction", scenario.get("redactions", [])),
            ("receipts", "receipt", scenario.get("receipts", [])),
            ("audiences", "audience", rooms),
            # Departures: every room's `left` members part, then deactivations,
            # then archives (a blocked room accepts no further events).
            ("departures", "leave", rooms),
            ("departures", "deactivate", [u for u in users if u.get("deactivated")]),
            ("departures", "archive", [r for r in rooms if r.get("archived")]),
        ]
        specs = [(phase, kind, spec) for phase, kind, group in kinds for spec in group]
        return [
            Step(index, phase, kind, spec)
            for index, (phase, kind, spec) in enumerate(specs)
        ]

    @classmethod
    def phases(cls, scenario: JsonDict) -> dict[str, int]:
        """The phases ``scenario`` runs, in order, with each one's step count.

        Args:
            scenario (JsonDict): The scenario request (a resource DAG).

        Returns:
            dict[str, int]: Phase name -> number of steps.
        """
        counts = dict.fromkeys(PHASES, 0)
        for step in cls.plan(scenario):
            counts[step.phase] += 1
        return counts

    def validate(self, scenario: JsonDict) -> None:
        """Reject a scenario that would fail for a reason knowable up front.

        Args:
            scenario (JsonDict): The scenario request (a resource DAG).

        Raises:
            SynapseError: 400 if ``run_id`` is missing, a user spec is invalid, a ref is undeclared, or an archived room's creator also departs.
        """
        if not scenario.get("run_id"):
            raise SynapseError(
                HTTPStatus.BAD_REQUEST, "run_id is required", Codes.MISSING_PARAM
            )
        for spec in scenario.get("users", []):
            self.users.validate(spec)
        deactivated_refs = self._deactivated_refs(scenario)
        for spec in scenario.get("rooms", []):
            self._validate_archiver(spec, deactivated_refs)
        check_refs(scenario)

    async def execute(
        self, deployment: Deployment, checkpoint: Checkpoint, journal: Journal
    ) -> Checkpoint:
        """Run ``deployment``'s scenario from ``checkpoint`` to the end.

        Provisioning is atomic: it returns only once every step has run, or it
        rolls back exactly what this deployment created and raises. A failure
        caused by the process shutting down is the exception: it is left for
        the deployment to resume once the process is back.

        Args:
            deployment (Deployment): The deployment to run.
            checkpoint (Checkpoint): Where it got to; a fresh one to start from scratch. Updated in place as steps finish.
            journal (Journal): Told of every step; it records checkpoints and may stop the deployment.

        Returns:
            Checkpoint: The finished checkpoint, describing everything provisioned.

        Raises:
            Exception: Propagated from a failing step (or from ``journal``, to stop the deployment), after rollback.
        """
        plan = self.plan(deployment.scenario)
        run = _Run(
            deployment=deployment,
            run_prefix=self.run_prefix(deployment.run_id),
            deactivated_refs=self._deactivated_refs(deployment.scenario),
            first_index=checkpoint.next_index,
            resumed=checkpoint.next_index > 0 or bool(checkpoint.created_users),
        )
        step = None
        try:
            for step in plan[checkpoint.next_index :]:
                await journal.before_step(step, checkpoint)
                await self._run_step(step, checkpoint, run, journal)
                checkpoint.next_index = step.index + 1
                await journal.after_step(step, checkpoint)
        except Exception:
            if journal.may_roll_back():
                await self._rollback(checkpoint, step, run)
            raise
        return checkpoint

    def graph(self, deployment: Deployment, checkpoint: Checkpoint) -> JsonDict:
        """What a finished deployment provisioned, keyed by scenario ref.

        Args:
            deployment (Deployment): The deployment.
            checkpoint (Checkpoint): Its finished checkpoint.

        Returns:
            JsonDict: ``run_id`` and the ``users``, ``rooms`` and ``events`` maps.
        """
        return {
            "run_id": deployment.run_id,
            "users": {ref: dict(user) for ref, user in checkpoint.users.items()},
            "rooms": dict(checkpoint.rooms),
            "events": dict(checkpoint.events),
        }

    async def issue_credentials(
        self, scenario: JsonDict, checkpoint: Checkpoint
    ) -> dict[str, JsonDict]:
        """Mint each provisioned user's credentials, as its ``auth`` calls for.

        Args:
            scenario (JsonDict): The scenario, for each user's ``auth``.
            checkpoint (Checkpoint): The finished checkpoint, for each user's mxid.

        Returns:
            dict[str, JsonDict]: User ref -> its credentials (empty for ``oidc``).
        """
        credentials = {}
        for spec in scenario.get("users", []):
            ref = spec.get("ref")
            user = checkpoint.users.get(ref)
            if user is not None:
                credentials[ref] = await self.users.issue_credentials(
                    spec, user["mxid"]
                )
        return credentials

    async def deprovision(self, checkpoint: Checkpoint) -> JsonDict:
        """Remove exactly what a deployment created. Safe to repeat.

        Args:
            checkpoint (Checkpoint): The deployment's checkpoint.

        Returns:
            JsonDict: ``deactivated`` (users) and ``rooms_purged`` counts.
        """
        rooms = await self.room_cleanup.purge(checkpoint.created_rooms)
        users = await self.cleanup.deactivate(checkpoint.created_users)
        return {"deactivated": users, "rooms_purged": rooms}

    async def _run_step(
        self, step: Step, checkpoint: Checkpoint, run: _Run, journal: Journal
    ) -> None:
        # Run one step, retrying it through a dependency that is briefly down
        # (e.g. mid-redeploy). Anything else fails the deployment.
        backoff = Backoff(self._retry_budget_s)
        while True:
            try:
                await self.steps.run(partial(self._apply, step, checkpoint, run))
                return
            except Exception as error:
                delay = backoff.next_delay() if is_transient(error) else None
                if delay is None:
                    raise
                logger.warning(
                    "fixtures step %s (%s) of deployment %s hit a transient "
                    "error, retrying in %ss: %s",
                    step.index,
                    step.kind,
                    run.deployment.id,
                    delay,
                    error,
                )
                await self.clock.sleep(delay)
                await journal.before_step(step, checkpoint)

    async def _apply(self, step: Step, checkpoint: Checkpoint, run: _Run) -> None:
        # Apply one step to the world, recording what it made in `checkpoint`.
        spec = step.spec
        deployment = run.deployment
        users, rooms, events = checkpoint.users, checkpoint.rooms, checkpoint.events
        if step.kind == "user":
            result = await self.users.provision(
                spec,
                deployment.run_id,
                run.run_prefix,
                deployment.id,
                deployment.admitted_ms,
            )
            users[result["ref"]] = result
            checkpoint.add_created_user(result["mxid"])
        elif step.kind == "room":
            await self._provision_room(step, checkpoint, run)
        elif step.kind in ("event", "media"):
            provisioner = self.events if step.kind == "event" else self.media
            args = (
                (spec, users, rooms, events)
                if step.kind == "event"
                else (spec, users, rooms)
            )
            result = await provisioner.provision(*args, txn_id=run.txn_id(step))
            # Media is a message too, so it joins the events map: a later
            # reaction, redaction or receipt can name it by ref.
            if result["ref"]:
                events[result["ref"]] = result["event_id"]
        elif step.kind == "reaction":
            await self.reactions.provision(
                spec, users, rooms, events, txn_id=run.txn_id(step)
            )
        elif step.kind == "redaction":
            await self.redactions.provision(
                spec, users, rooms, events, txn_id=run.txn_id(step)
            )
        elif step.kind == "receipt":
            await self.receipts.provision(spec, users, rooms, events)
        elif step.kind == "audience":
            # CPG evicts anyone an audience context does not name and converges
            # asynchronously, so the context lands once every event is sent.
            await self.audience.provision(
                spec, users, rooms[spec["ref"]], run.deactivated_refs
            )
        elif step.kind == "leave":
            await self.members.leave(spec, users, rooms[spec["ref"]])
        elif step.kind == "deactivate":
            await self.users.deactivate(users[spec["ref"]]["mxid"])
        elif step.kind == "archive":
            await self.archive.archive(
                rooms[spec["ref"]], users[spec["creator"]]["mxid"]
            )

    async def _provision_room(
        self, step: Step, checkpoint: Checkpoint, run: _Run
    ) -> None:
        spec = step.spec
        users = checkpoint.users
        if not run.audiences_validated:
            # Audience users need an external_id, known only once users exist;
            # checked here so a bad audience fails before any room or upload.
            for room in run.deployment.scenario.get("rooms", []):
                self.audience.validate(room, users, run.deactivated_refs)
            run.audiences_validated = True

        if run.resumed and step.index == run.first_index:
            # A restart may have cut this room's creation off part-way, leaving
            # a room nothing refers to yet. Clear it, then create it whole.
            strays = await self._stray_rooms(step, checkpoint, run)
            await self.room_cleanup.purge(strays)

        result = await self.rooms.provision(
            spec, users, run.deployment.run_id, run.deployment.id
        )
        checkpoint.add_created_room(result["room_id"])
        checkpoint.rooms[result["ref"]] = result["room_id"]
        await self.members.provision(spec, users, result["room_id"])

    async def _stray_rooms(
        self, step: Step, checkpoint: Checkpoint, run: _Run
    ) -> list[str]:
        # Rooms this deployment created for `step` that its checkpoint doesn't
        # know about: made, but cut off (or failed) before being recorded.
        creator = resolve_user_ref(
            step.spec.get("creator"), checkpoint.users, "creator"
        )
        found = await self.rooms.created_by(
            creator, run.deployment.id, step.spec.get("ref")
        )
        return [room_id for room_id in found if room_id not in checkpoint.created_rooms]

    async def _stray_user(self, step: Step, run: _Run) -> list[str]:
        # The user a failed user step made but never recorded, if any: the
        # account at its localpart, if this deployment marked it as its own.
        spec = step.spec
        localpart = (spec.get("identity") or {}).get(
            "localpart"
        ) or self.users.random_localpart(
            run.run_prefix, run.deployment.id, spec.get("ref")
        )
        mxid = UserID(localpart, self.hs.hostname).to_string()
        marker = await self.store.get_global_account_data_by_type_for_user(
            mxid, RUN_MARKER
        )
        return [mxid] if deployment_from_marker(marker) == run.deployment.id else []

    async def _rollback(
        self, checkpoint: Checkpoint, failed_step: Step | None, run: _Run
    ) -> None:
        # Best-effort teardown of exactly what this deployment created — what
        # its checkpoint lists, plus whatever the failing step made before it
        # failed. Rollback errors are logged, never raised: the original error
        # is what the caller needs. It runs outside `steps` (no timeout), so it
        # is never cut short.
        rooms = list(checkpoint.created_rooms)
        users = list(checkpoint.created_users)
        try:
            if failed_step is not None and failed_step.kind == "room":
                rooms += await self._stray_rooms(failed_step, checkpoint, run)
            if failed_step is not None and failed_step.kind == "user":
                users += [
                    mxid
                    for mxid in await self._stray_user(failed_step, run)
                    if mxid not in users
                ]
        except Exception:
            logger.exception(
                "fixtures rollback: finding the failed step's leftovers failed"
            )
        try:
            await self.room_cleanup.purge(rooms)
        except Exception:
            logger.exception("fixtures rollback: room purge failed")
        try:
            await self.cleanup.deactivate(users)
        except Exception:
            logger.exception("fixtures rollback: user deactivation failed")

    @staticmethod
    def _deactivated_refs(scenario: JsonDict) -> set:
        return {
            spec.get("ref")
            for spec in scenario.get("users", [])
            if spec.get("deactivated")
        }

    @staticmethod
    def _validate_archiver(spec: JsonDict, deactivated_refs: set) -> None:
        # The creator archives the room, so must still be joined when archiving
        # runs, after the `left` parts and deactivations.
        if not spec.get("archived"):
            return
        creator = spec.get("creator")
        if creator in spec.get("left", []) or creator in deactivated_refs:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"archived room '{spec.get('ref')}' is archived by its creator, "
                "who cannot also leave or be deactivated",
                Codes.INVALID_PARAM,
            )

    async def inventory(self) -> JsonDict:
        """Every fixtures resource currently up, grouped by the run that owns it.

        Read-only: lists what is provisioned without tearing anything down. A
        run appears only if it still owns at least one resource.

        Returns:
            JsonDict: ``runs`` maps each run_id to its ``users`` and ``rooms``.
        """
        runs: dict[str, JsonDict] = {}

        def bucket(run_id: str) -> JsonDict:
            return runs.setdefault(run_id, {"users": [], "rooms": []})

        for mxid, run_id in await self.cleanup.inventory():
            if run_id:
                bucket(run_id)["users"].append(mxid)
        for room_id, run_id in await self.room_cleanup.inventory():
            if run_id:
                bucket(run_id)["rooms"].append(room_id)

        return {"runs": runs}

    async def cleanup_run(self, run_id: str) -> JsonDict:
        """Tear down every resource provisioned for ``run_id``.

        Args:
            run_id (str): The id of the run to tear down.

        Returns:
            JsonDict: ``deactivated`` (users) and ``rooms_purged`` counts.
        """
        rooms = await self.room_cleanup.cleanup_run(run_id)
        users = await self.cleanup.cleanup_run(run_id, self.run_prefix(run_id))
        return {"deactivated": users, "rooms_purged": rooms}

    async def cleanup_all(self) -> JsonDict:
        """Tear down every fixtures resource, across all runs.

        Returns:
            JsonDict: ``deactivated`` (users) and ``rooms_purged`` counts.
        """
        rooms = await self.room_cleanup.cleanup_all()
        users = await self.cleanup.cleanup_all()
        return {"deactivated": users, "rooms_purged": rooms}
