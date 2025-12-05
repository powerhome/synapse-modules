"""Routes for corporate events."""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import HttpResponseException
from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict

if TYPE_CHECKING:
    from synapse.server import HomeServer


class AppointmentBlockResource(JsonResource):
    def __init__(self, hs: "HomeServer", config: dict):
        JsonResource.__init__(self, hs, canonical_json=False)
        self.register_servlets(self, hs, config)

    @staticmethod
    def register_servlets(resource: HttpServer, hs: "HomeServer", config: dict) -> None:
        AppointmentBlockServlet(hs, config).register(resource)


class AppointmentBlockServlet(RestServlet):
    PATTERNS = [re.compile("^/_nitro/appointment_block/(?P<appointment_id>[^/]*)")]
    CATEGORY = "Appointment Block requests"

    def __init__(self, hs: "HomeServer", config):
        super().__init__()
        self.auth = hs.get_auth()
        self.hs = hs
        self.token = config["hs_token"].encode("ascii")
        self.bridge_base_url = config["bridge_base_url"]

    async def on_GET(
        self, request: SynapseRequest, appointment_id: str
    ) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)

        uri = f"{self.bridge_base_url}/_nitro/appointment_block"
        headers = {b"Authorization": [b"Bearer " + self.token]}

        try:
            args = {
                "user_id": requester.user.to_string(),
                "appointment_id": appointment_id,
            }
            body = await self.hs.get_simple_http_client().get_json(
                uri, args=args, headers=headers
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        return HTTPStatus.OK, body
