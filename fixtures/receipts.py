"""Provisions read receipts through the receipts handler."""

from synapse.api.constants import ReceiptTypes
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict, UserID

from connect.fixtures.auth import authenticate_fixtures_request
from connect.fixtures.refs import resolve_event_ref, resolve_room_ref, resolve_user_ref


class ReceiptProvisioner:
    """Records a user's ``m.read`` receipt at a provisioned event.

    Nothing in the ``events`` provisioner marks a message as read, so a scenario
    that needs a user parked mid-history (the shape the CCL timeline-anchoring
    E2E reads against: unread messages after a read receipt) has no way to
    express it. This provisioner adds one: a ``receipts`` entry names a
    ``room``, a ``user``, and the ``event`` they have read up to, and this
    records that receipt through the same handler the server itself uses
    (``connect/receipts``). Receipts own no resource of their own — they live on
    the room, so purging the room (run teardown) removes them, exactly like an
    event.

    In worker deployments receipts have a dedicated stream writer
    (``stream_writers.receipts``), and Synapse's receipts store refuses writes
    from every other instance — including the main process, where the fixtures
    scenario servlet runs. So ``record`` writes through the local receipts
    handler only when this instance is a receipts writer; otherwise it forwards
    the already-resolved receipt to the ``/_fixtures/receipt`` endpoint the
    fixtures module registers on the writer (see ``connect/fixtures``),
    authenticated with the same fixtures shared secret.
    """

    def __init__(self, hs, shared_secret: str):
        self.hs = hs
        self._secret = shared_secret
        self.receipts_handler = hs.get_receipts_handler()
        self._http = hs.get_simple_http_client()

    def authenticate(self, request: SynapseRequest) -> None:
        """Reject ``request`` unless it carries the fixtures shared secret.

        Args:
            request (SynapseRequest): The incoming request.
        """
        authenticate_fixtures_request(request, self._secret)

    async def provision(
        self,
        spec: JsonDict,
        users: dict[str, JsonDict],
        rooms: dict[str, str],
        events: dict[str, str],
    ) -> None:
        """Record the read receipt described by ``spec``.

        Args:
            spec (JsonDict): The receipt spec — ``room`` (a room ref), ``user`` (a user ref), and ``event`` (an event ref the user has read up to).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref, used to resolve ``user``.
            rooms (dict[str, str]): The scenario's provisioned rooms (ref -> room_id), used to resolve ``room``.
            events (dict[str, str]): The events already provisioned in this scenario (ref -> event_id), used to resolve ``event``.
        """
        room_id = resolve_room_ref(spec.get("room"), rooms, "receipt room")
        user_id = resolve_user_ref(spec.get("user"), users, "receipt user")
        event_id = resolve_event_ref(spec.get("event"), events, "receipt event")

        await self.record(room_id=room_id, user_id=user_id, event_id=event_id)

    async def record(self, room_id: str, user_id: str, event_id: str) -> None:
        """Record the receipt on whichever instance owns the receipts stream.

        Args:
            room_id (str): The room the receipt lives in.
            user_id (str): The mxid of the user the receipt belongs to.
            event_id (str): The event the user has read up to.
        """
        writers = self.hs.config.worker.writers.receipts
        if self.hs.get_instance_name() in writers:
            # `received_client_receipt` requires a `UserID`, not the bare mxid
            # string — it calls `.to_string()` on it internally.
            await self.receipts_handler.received_client_receipt(
                room_id=room_id,
                receipt_type=ReceiptTypes.READ,
                user_id=UserID.from_string(user_id),
                event_id=event_id,
                thread_id=None,
            )
            return

        await self._forward_to_writer(writers[0], room_id, user_id, event_id)

    async def _forward_to_writer(
        self, writer: str, room_id: str, user_id: str, event_id: str
    ) -> None:
        # The receipts stream allows multiple writers and any of them can record
        # a receipt, so the first is as good as any. Synapse validates at config
        # load that every writer appears in `instance_map`, so the lookup can't
        # miss. A non-2xx from the writer propagates as HttpResponseException,
        # failing the scenario after rollback like any other provisioner error.
        location = self.hs.config.worker.instance_map[writer]
        uri = f"{location.scheme()}://{location.netloc()}/_fixtures/receipt"
        await self._http.post_json_get_json(
            uri,
            {"room_id": room_id, "user_id": user_id, "event_id": event_id},
            headers={b"Authorization": [f"Bearer {self._secret}".encode("ascii")]},
        )
