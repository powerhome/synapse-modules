"""Orchestrates a provisioning scenario across its resource types."""

import hashlib
import logging
from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict

from connect.fixtures.auth import authenticate_fixtures_request
from connect.fixtures.cleanup import Cleanup, RoomCleanup
from connect.fixtures.directory import DirectoryEnricher
from connect.fixtures.events import EventProvisioner
from connect.fixtures.media import MediaProvisioner
from connect.fixtures.membership import MembershipProvisioner
from connect.fixtures.receipts import ReceiptProvisioner
from connect.fixtures.rooms import RoomProvisioner
from connect.fixtures.users import UserProvisioner

logger = logging.getLogger(__name__)


class Provisioner:
    """Resolves a scenario (a small resource DAG) into provisioned fixtures.

    Resolution order is users -> rooms -> memberships -> events -> receipts ->
    media, so later resources can reference earlier ones by their
    scenario-local ``ref``.
    Media is still a scaffolded stub that reports ``not yet implemented`` when a
    scenario asks for it; the rest are live.
    """

    def __init__(self, hs, shared_secret: str, idp_id=None, audiences_base_url=None):
        self.hs = hs
        self.store = hs.get_datastores().main
        self._secret = shared_secret

        directory = (
            DirectoryEnricher(hs, audiences_base_url) if audiences_base_url else None
        )
        self.users = UserProvisioner(hs, idp_id=idp_id, directory=directory)
        self.rooms = RoomProvisioner(hs)
        self.members = MembershipProvisioner(hs)
        self.events = EventProvisioner(hs)
        self.receipts = ReceiptProvisioner(hs, shared_secret)
        self.media = MediaProvisioner(hs)
        self.cleanup = Cleanup(hs)
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

    async def provision_scenario(self, scenario: JsonDict) -> JsonDict:
        """Provision every resource in ``scenario`` and return the graph.

        Provisioning is atomic: the call returns only once every resource is
        created, or it rolls back exactly what it created and raises. The caller
        never has to clean up partial state.

        Args:
            scenario (JsonDict): The scenario request (a resource DAG).

        Returns:
            JsonDict: The provisioned graph keyed by resource ``ref``.

        Raises:
            SynapseError: If ``run_id`` is missing.
            Exception: Propagated from a failing resource provisioner (e.g. a Fixed-name conflict or an unimplemented resource type) after this call's partial state has been rolled back.
        """
        run_id = scenario.get("run_id")
        if not run_id:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST, "run_id is required", Codes.MISSING_PARAM
            )

        run_prefix = self.run_prefix(run_id)

        users_out: dict[str, JsonDict] = {}
        rooms_out: dict[str, str] = {}
        events_out: dict[str, str] = {}
        # Track exactly what THIS call creates so a failure rolls back only its
        # own resources — not other scenarios sharing the same run_id.
        created_users: list[str] = []
        created_rooms: list[str] = []
        try:
            for spec in scenario.get("users", []):
                result = await self.users.provision(spec, run_id, run_prefix)
                created_users.append(result["mxid"])
                users_out[result["ref"]] = result

            for spec in scenario.get("rooms", []):
                result = await self.rooms.provision(spec, users_out, run_id)
                created_rooms.append(result["room_id"])
                rooms_out[result["ref"]] = result["room_id"]
                await self.members.provision(spec, users_out, result["room_id"])

            # Events are seeded after memberships so a sender is already joined,
            # and accumulated into `events_out` so a later event can `reply_to`
            # an earlier one by ref. An event with no `ref` simply isn't tracked.
            for spec in scenario.get("events", []):
                result = await self.events.provision(
                    spec, users_out, rooms_out, events_out
                )
                if result["ref"]:
                    events_out[result["ref"]] = result["event_id"]

            # Receipts are seeded after events so a receipt can name the event
            # it reads up to by ref. They own no resource of their own (a
            # receipt lives on its room), so there's nothing to track for
            # rollback — purging the room removes them.
            for spec in scenario.get("receipts", []):
                await self.receipts.provision(spec, users_out, rooms_out, events_out)

            # TODO: media is a stub — a non-empty section reports
            #       not-yet-implemented until the provisioner is built (Phase 6
            #       world layers). The contract already accepts the field.
            for spec in scenario.get("media", []):
                await self.media.provision(spec, users_out)
        except Exception:
            await self._rollback(created_rooms, created_users)
            raise

        return {
            "run_id": run_id,
            "users": users_out,
            "rooms": rooms_out,
            "events": events_out,
        }

    async def _rollback(self, room_ids: list[str], user_mxids: list[str]) -> None:
        # Best-effort teardown of exactly what a failed scenario created, so a
        # failed POST leaves no partial state. Rollback errors are logged, never
        # raised — the original provisioning error is what the caller needs.
        try:
            await self.room_cleanup.purge(room_ids)
        except Exception:
            logger.exception("fixtures rollback: room purge failed")
        try:
            await self.cleanup.deactivate(user_mxids)
        except Exception:
            logger.exception("fixtures rollback: user deactivation failed")

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
