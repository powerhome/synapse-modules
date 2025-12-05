"""Overrides the Synapse push rule REST servlet."""

import logging
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import SynapseError, UnrecognizedRequestError
from synapse.http.site import SynapseRequest
from synapse.rest.client.push_rule import PushRuleRestServlet, _rule_spec_from_path
from synapse.types import JsonDict, RoomID

from .notification_preference_listener import NotificationPreferenceListener

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class ConnectPushRuleRestServlet(PushRuleRestServlet):
    """Custom push rule servlet for Connect."""

    def __init__(self, hs: "HomeServer", config: dict) -> None:
        """
        Initializes the ConnectPushRuleRestServlet.

        Args:
            hs (HomeServer): The HomeServer instance.
            config (dict): The configuration dictionary.
        """
        self._is_worker = hs.config.worker.worker_app is not None
        self.notification_listener = NotificationPreferenceListener(hs, config)
        super().__init__(hs)

    async def on_PUT(self, request: SynapseRequest, path: str) -> Tuple[int, JsonDict]:
        """
        Handles PUT requests for push rules.

        Args:
            request (SynapseRequest): The HTTP request.
            path (str): The request path.

        Returns:
            Tuple[int, JsonDict]: The HTTP status and response body.

        Raises:
            Exception: If the request is made on a worker.
        """
        if self._is_worker:
            raise Exception("Cannot handle PUT /push_rules on worker")

        requester = await self.auth.get_user_by_req(request)
        user_id = requester.user.to_string()

        async with self._push_rule_linearizer.queue(user_id):
            synapse_response = await self.handle_put(request, path, user_id)

        try:
            spec = _rule_spec_from_path(path.split("/"))
            rule_id_parts = spec.rule_id.split(";")
            raw_room_id = rule_id_parts[1]
            room_id = RoomID.from_string(raw_room_id)

            await self.notification_listener.on_push_rules_changed(user_id, room_id)
        except (AttributeError, IndexError, SynapseError, UnrecognizedRequestError):
            logger.warning(
                f"Unable to parse room_id from {path}. Not calling on_push_rules_changed for {user_id}"
            )

        return synapse_response
