"""Notification Preferences API."""

import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import SynapseError
from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.types import JsonDict

from .bridge_client import BridgeClient
from .notification_preferences import Level
from .push_rule_manager import PushRuleManager

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class NotificationPreferencesResource(JsonResource):
    """A resource for managing notification preferences."""

    def __init__(self, hs: "HomeServer", config: dict):
        JsonResource.__init__(self, hs, canonical_json=False)
        self.register_servlets(self, hs, config)

    @staticmethod
    def register_servlets(
        resource: HttpServer,
        hs: "HomeServer",
        config: dict,
    ) -> None:
        NotificationPreferencesServlet(hs, config).register(resource)


class NotificationPreferencesServlet(RestServlet):
    """A servlet for managing notification preferences."""

    PATTERNS = [re.compile("^/notification_preferences.*")]
    CATEGORY = "Notification preferences requests"

    def __init__(self, hs: "HomeServer", config: dict):
        super().__init__()
        self.auth = hs.get_auth()
        self.hs = hs
        self.config = config

    async def on_PUT(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        content = parse_json_object_from_request(request)
        requester = await self.auth.get_user_by_req(request)
        user_id = requester.user.to_string()

        matrix_room_id = content.get("matrix_room_id")
        if not matrix_room_id:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "Missing matrix_room_id param in request",
            )

        requested_level = content.get("level")
        valid_levels_message = (
            f"level must be one of {[member.value for member in Level]}"
        )
        if not requested_level:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"Missing level param in request. {valid_levels_message}",
            )

        try:
            level = Level(requested_level)
        except ValueError:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"Invalid level: {requested_level}. {valid_levels_message}",
            )

        await PushRuleManager(
            self.hs,
            room_id=matrix_room_id,
            user_id=user_id,
            notification_preference_event_type=self.config[
                "notification_preference_event_type"
            ],
        ).update_to_level(level)

        bridge_header_present = request.getHeader(b"connect-bridge") is not None
        success_msg = {
            "message": "Notification preferences updated successfully",
            "level": level.value,
        }

        bridge_config = self.config.get("bridge")
        # If the bridge is not configured for this environment: return early
        if not bridge_config:
            return HTTPStatus.OK, success_msg

        # If the request was sent across the bridge: return early
        if bridge_header_present:
            return HTTPStatus.OK, success_msg

        # The request originated on v3, and the bridge is configured: update the bridge before returning.
        bridge_client = BridgeClient(self.hs, bridge_config)
        await bridge_client.update_preference(
            matrix_user_id=user_id,
            matrix_room_id=matrix_room_id,
            content={"level": level.value},
        )

        return HTTPStatus.OK, success_msg
