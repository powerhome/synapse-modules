"""Routes for Account Provisioning"""

import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import Codes, SynapseError
from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.types import JsonDict

from ..helpers.request_context import init_request_context, log_event
from .populator import Populator

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


def validate_scim_user_payload(user: JsonDict) -> None:
    """Validate SCIM user payload format and required fields.

    Args:
        user: The SCIM user payload to validate

    Raises:
        SynapseError: If validation fails
    """
    required_fields = ["id", "userName", "displayName", "active"]
    for field in required_fields:
        if field not in user:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"Missing required SCIM field: '{field}'",
                Codes.BAD_JSON,
            )

    if not isinstance(user.get("active"), bool):
        raise SynapseError(
            HTTPStatus.BAD_REQUEST,
            (
                f"Field 'active' must be boolean, got "
                f"{type(user.get('active')).__name__}"
            ),
            Codes.BAD_JSON,
        )

    if "groups" in user and not isinstance(user["groups"], list):
        raise SynapseError(
            HTTPStatus.BAD_REQUEST,
            f"Field 'groups' must be array, got {type(user['groups']).__name__}",
            Codes.BAD_JSON,
        )


class ProvisioningResource(JsonResource):
    """Json Resource registration for provisioning servlet."""

    def __init__(self, hs: "HomeServer", populator: Populator):
        JsonResource.__init__(self, hs, canonical_json=False, extract_context=True)
        self.register_servlets(self, hs, populator)

    @staticmethod
    def register_servlets(
        resource: HttpServer, hs: "HomeServer", populator: Populator
    ) -> None:
        ProvisioningServlet(hs, populator).register(resource)


class ProvisioningServlet(RestServlet):
    """Servlet to process account provisioning requests."""

    PATTERNS = [re.compile("^/_populator/user$")]
    CATEGORY = "User Provisioning Interface"

    def __init__(self, hs: "HomeServer", populator: Populator):
        super().__init__()
        self.auth = hs.get_auth()
        self.populator = populator

    async def on_POST(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        try:
            await self._authenticate_audiences_bot(request)

            init_request_context(request, action="provision_single_user")
            log_event(
                logger, "Provisioning request received", endpoint="/_populator/user"
            )

            user = parse_json_object_from_request(request)
            validate_scim_user_payload(user)
            user_id = user.get("id")
            active = user.get("active")

            log_event(
                logger,
                "Provisioning single user",
                user_id=user_id,
                active=active,
            )

            await self.populator.populate_single_user(user)

            log_event(
                logger,
                "Provisioning completed",
                user_id=user_id,
                active=active,
            )
            return HTTPStatus.NO_CONTENT, {}
        except SynapseError as e:
            if e.code == HTTPStatus.BAD_REQUEST:
                log_event(logger, "Validation failed", level="error", error=e.msg)
            raise

    async def _authenticate_audiences_bot(self, request: SynapseRequest):
        requester = await self.auth.get_user_by_req(request)
        if requester.user.localpart != "audiences_bot":
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                f"{requester.user.localpart} cannot use user provisioning",
                Codes.FORBIDDEN,
            )
