"""Routes for Account Provisioning"""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import Codes, SynapseError
from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.types import JsonDict

from .populator import Populator

if TYPE_CHECKING:
    from synapse.server import HomeServer


class ProvisioningResource(JsonResource):
    """Json Resource registration for provisioning servlet."""

    def __init__(self, hs: "HomeServer", populator: Populator):
        JsonResource.__init__(self, hs, canonical_json=False)
        self.register_servlets(self, hs, populator)

    @staticmethod
    def register_servlets(
        resource: HttpServer, hs: "HomeServer", populator: Populator
    ) -> None:
        ProvisioningServlet(hs, populator).register(resource)


class ProvisioningServlet(RestServlet):
    """Servlet to process account provisioning requests."""

    PATTERNS = [re.compile("^/_populator/users$")]
    CATEGORY = "User Provisioning Interface"

    def __init__(self, hs: "HomeServer", populator: Populator):
        super().__init__()
        self.auth = hs.get_auth()
        self.populator = populator

    async def on_POST(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        await self._authenticate_audiences_bot(request)

        user = parse_json_object_from_request(request)
        await self.populator.populate_single_user(user)

        return HTTPStatus.NO_CONTENT, {}

    async def _authenticate_audiences_bot(self, request: SynapseRequest):
        requester = await self.auth.get_user_by_req(request)
        if requester.user.localpart != "audiences_bot":
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                f"{requester.user.localpart} cannot use webhook",
                Codes.FORBIDDEN,
            )
