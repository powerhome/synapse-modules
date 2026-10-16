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
        self.processing: defer.Deferred = None
        self.cached_users: list[JsonDict] = None

    async def on_POST(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        await self._validate_user(request)

        init_request_context(request, action="populate_all_users")
        log_event(
            logger,
            "Bulk user provisioning request received",
            endpoint="/_populator/users",
        )

        users = parse_json_object_from_request(request)["users"]
        users = [user for user in users if user.get("active")]

        if encode_canonical_json(users) == encode_canonical_json(self.cached_users):
            log_event(
                logger, "No user changes to populate, skipping", user_count=len(users)
            )
            return HTTPStatus.NO_CONTENT, {}

        self.cached_users = users

        if not self.processing or self.processing.called:
            log_event(
                logger,
                "Scheduling background populate",
                user_count=len(users),
            )
            self.processing = self.api.run_as_background_process(
                "populate_all_users",
                self.populator.populate_all_users,
                users,
            )
        else:
            log_event(
                logger,
                "Populate already in progress, skipping",
                user_count=len(users),
            )

        return HTTPStatus.NO_CONTENT, {}

    async def _validate_user(self, request: SynapseRequest):
        requester = await self.auth.get_user_by_req(request)
        if requester.user.localpart != "audiences_bot":
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                f"{requester.user.localpart} cannot use bulk user provisioning",
                Codes.FORBIDDEN,
            )
