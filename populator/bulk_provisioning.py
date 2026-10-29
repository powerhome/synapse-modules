"""Routes for bulk user provisioning."""

import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from canonicaljson import encode_canonical_json
from synapse.api.errors import Codes, SynapseError
from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.types import JsonDict
from twisted.internet import defer
from twisted.python.failure import Failure

from ..helpers.request_context import init_request_context, log_event
from .populator import Populator

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class BulkUserProvisioningResource(JsonResource):
    """Json Resource registration for bulk provisioning servlet."""

    def __init__(self, hs: "HomeServer", populator: Populator):
        JsonResource.__init__(self, hs, canonical_json=False, extract_context=True)
        self.register_servlets(self, hs, populator)

    @staticmethod
    def register_servlets(
        resource: HttpServer, hs: "HomeServer", populator: Populator
    ) -> None:
        BulkUserProvisioningServlet(hs, populator).register(resource)


class BulkUserProvisioningServlet(RestServlet):
    """Servlet to process account provisioning requests."""

    PATTERNS = [re.compile("^/_populator/users$")]
    CATEGORY = "Audience bulk user provisioning requests"

    def __init__(self, hs: "HomeServer", populator: Populator):
        super().__init__()
        self.api = hs.get_module_api()
        self.auth = hs.get_auth()
        self.populator = populator
        # Deferred for the populate currently running (None before the first run).
        self.processing: defer.Deferred = None
        # Canonical snapshot of the last payload actually scheduled. Used to
        # dedupe no-op requests. Only set for payloads that were scheduled.
        self.cached_users: list[JsonDict] = None
        # Latest snapshot received while a run was in progress, queued to run
        # next. Overwritten by each newer overlapping request (latest wins).
        self.pending_users: list[JsonDict] = None
        # Highest version token accepted so far (in-flight or pending). Used to
        # reject a snapshot that was computed earlier but arrives later, so the
        # latest-computed snapshot wins rather than the latest-arriving one.
        self.latest_version: int = None

    async def on_POST(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        await self._validate_user(request)

        init_request_context(request, action="populate_all_users")
        log_event(
            logger,
            "Bulk user provisioning request received",
            endpoint="/_populator/users",
        )

        body = parse_json_object_from_request(request)
        users = [user for user in body["users"] if user.get("active")]
        version = body.get("version")

        if (
            isinstance(version, int)
            and self.latest_version is not None
            and version <= self.latest_version
        ):
            # A newer-or-equal snapshot has already been accepted. This one was
            # computed earlier, so applying it would revert fresher state.
            log_event(
                logger,
                "Stale snapshot ignored",
                version=version,
                latest_version=self.latest_version,
            )
            return HTTPStatus.NO_CONTENT, {}

        if encode_canonical_json(users) == encode_canonical_json(self.cached_users):
            # The desired state already matches the latest scheduled snapshot
            # (either currently running or already applied). Drop any stale
            # snapshot queued behind the current run so it is not applied after.
            if self.pending_users is not None:
                log_event(
                    logger,
                    "Queued populate superseded by matching request, discarding",
                    user_count=len(users),
                )
                self.pending_users = None
            self._record_version(version)
            log_event(
                logger, "No user changes to populate, skipping", user_count=len(users)
            )
            return HTTPStatus.NO_CONTENT, {}

        if self.processing is not None and not self.processing.called:
            # A run is in flight. Queue this snapshot (replacing any older one)
            # to run when the current populate finishes.
            log_event(
                logger,
                "Populate in progress, queuing latest snapshot",
                user_count=len(users),
            )
            self.pending_users = users
            self._record_version(version)
            return HTTPStatus.NO_CONTENT, {}

        self._record_version(version)
        self._schedule_populate(users)
        return HTTPStatus.NO_CONTENT, {}

    def _record_version(self, version) -> None:
        # Ignore a missing or malformed token (older caller) and keep the
        # latest-arriving behaviour for those requests.
        if isinstance(version, int):
            self.latest_version = version

    def _schedule_populate(self, users: list[JsonDict]) -> None:
        log_event(
            logger,
            "Scheduling background populate",
            user_count=len(users),
        )
        self.cached_users = users
        self.processing = self.api.run_as_background_process(
            "populate_all_users",
            self.populator.populate_all_users,
            users,
        )
        self.processing.addBoth(self._on_populate_complete)

    def _on_populate_complete(self, result):
        if isinstance(result, Failure):
            log_event(
                logger,
                "Background populate failed",
                level=logging.ERROR,
                error=result.getErrorMessage(),
            )
            # Clear the dedupe cache so an identical retry re-runs rather than
            # being skipped as a no-op.
            self.cached_users = None

        if self.pending_users is not None:
            pending = self.pending_users
            self.pending_users = None
            log_event(
                logger,
                "Draining queued populate snapshot",
                user_count=len(pending),
            )
            self._schedule_populate(pending)

        # Consume any Failure so it is not logged as unhandled by Twisted.
        return None

    async def _validate_user(self, request: SynapseRequest):
        requester = await self.auth.get_user_by_req(request)
        if requester.user.localpart != "audiences_bot":
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                f"{requester.user.localpart} cannot use bulk user provisioning",
                Codes.FORBIDDEN,
            )
