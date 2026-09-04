"""Module that overrides receipt and read marker logic."""

import logging
from typing import TYPE_CHECKING, Tuple

from synapse.api.constants import ReceiptTypes
from synapse.http.servlet import parse_json_object_from_request
from synapse.http.site import SynapseRequest
from synapse.rest.client.read_marker import ReadMarkerRestServlet
from synapse.types import JsonDict

from .badge import BadgeHandler

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class ConnectReadMarkerRestServlet(ReadMarkerRestServlet):
    """
    A subclass that fixes the badge count after updating the read marker.

    We set the badge count because clients do not always make the necessary POST /receipt request.
    """

    def __init__(self, hs: "HomeServer"):
        super().__init__(hs)
        self.badge_handler = BadgeHandler(hs)

    async def on_POST(
        self, request: SynapseRequest, room_id: str
    ) -> Tuple[int, JsonDict]:
        # We cannot simply call super().on_POST because that method also calls get_user_by_req.
        #
        # Synapse prevents calling get_user_by_req more than once for a given request, failing with an AssertionError.
        #
        # See https://github.com/element-hq/synapse/blob/v1.138.2/synapse/http/site.py#L204-L205 for the code.
        requester = await self.auth.get_user_by_req(request)

        await self.presence_handler.bump_presence_active_time(
            requester.user, requester.device_id
        )

        body = parse_json_object_from_request(request)

        unrecognized_types = set(body.keys()) - self._known_receipt_types
        if unrecognized_types:
            # It's fine if there are unrecognized receipt types, but let's log
            # it to help debug clients that have typoed the receipt type.
            #
            # We specifically *don't* error here, as a) it stops us processing
            # the valid receipts, and b) we need to be extensible on receipt
            # types.
            logger.info("Ignoring unrecognized receipt types: %s", unrecognized_types)

        for receipt_type in self._known_receipt_types:
            event_id = body.get(receipt_type, None)
            # TODO Add validation to reject non-string event IDs.
            if not event_id:
                continue

            if receipt_type == ReceiptTypes.FULLY_READ:
                await self.read_marker_handler.received_client_read_marker(
                    room_id,
                    user_id=requester.user.to_string(),
                    event_id=event_id,
                )
            else:
                await self.receipts_handler.received_client_receipt(
                    room_id,
                    receipt_type,
                    user_id=requester.user,
                    event_id=event_id,
                    # Setting the thread ID is not possible with the /read_markers endpoint.
                    thread_id=None,
                )

        # PATCHED
        await self.badge_handler.fix_count(requester.user, room_id)

        return 200, {}
