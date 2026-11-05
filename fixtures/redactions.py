"""Provisions redactions (m.room.redaction) through the event handler."""

from synapse.api.constants import EventTypes
from synapse.types import JsonDict

from connect.fixtures.refs import resolve_event_ref, resolve_room_ref, resolve_user_ref
from connect.fixtures.sending import send_once


class RedactionProvisioner:
    """Redacts a provisioned event, as a provisioned user.

    A redaction is an ``m.room.redaction`` event naming the event it redacts,
    sent through the same in-process event-creation handler as
    ``EventProvisioner``, so Synapse's own auth applies: the sender must be the
    event's sender or hold the redact power level. Like any event, a redaction
    lives on its room, so purging the room (run teardown) removes it.
    """

    def __init__(self, hs):
        self.hs = hs
        self.event_creation_handler = hs.get_event_creation_handler()

    async def provision(
        self,
        spec: JsonDict,
        users: dict[str, JsonDict],
        rooms: dict[str, str],
        events: dict[str, str],
        *,
        txn_id: str,
    ) -> JsonDict:
        """Send the redaction described by ``spec`` and return its event id.

        Args:
            spec (JsonDict): The redaction spec — ``room`` (a room ref), ``sender`` (a user ref) and ``event`` (the event ref to redact).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref, used to resolve ``sender``.
            rooms (dict[str, str]): The scenario's provisioned rooms (ref -> room_id), used to resolve ``room``.
            events (dict[str, str]): The events already provisioned in this scenario (ref -> event_id), used to resolve ``event``.
            txn_id (str): Unique to this deployment step; makes a repeated send return the original.

        Returns:
            JsonDict: The created redaction's ``event_id``.
        """
        room_id = resolve_room_ref(spec.get("room"), rooms, "redaction room")
        sender = resolve_user_ref(spec.get("sender"), users, "redaction sender")
        target_event_id = resolve_event_ref(
            spec.get("event"), events, "redaction event"
        )

        event = await send_once(
            self.event_creation_handler,
            sender,
            {
                "type": EventTypes.Redaction,
                "room_id": room_id,
                "sender": sender,
                "content": {},
                "redacts": target_event_id,
            },
            txn_id,
        )
        return {"event_id": event.event_id}
