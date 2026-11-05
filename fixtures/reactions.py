"""Provisions reactions (m.reaction annotations) through the event handler."""

from http import HTTPStatus

from synapse.api.constants import EventTypes, RelationTypes
from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict

from connect.fixtures.refs import resolve_event_ref, resolve_room_ref, resolve_user_ref
from connect.fixtures.sending import send_once


class ReactionProvisioner:
    """Annotates a provisioned event with a reaction, as a provisioned user.

    A reaction is an ``m.reaction`` event whose ``m.relates_to`` is an
    ``m.annotation`` of the target event, sent through the same in-process
    event-creation handler as ``EventProvisioner``. Like a message, a reaction
    owns no resource of its own — it lives on its room, so purging the room
    (run teardown) removes it.
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
        """Send the reaction described by ``spec`` and return its event id.

        Args:
            spec (JsonDict): The reaction spec — ``room`` (a room ref), ``sender`` (a user ref), ``event`` (the event ref being reacted to), ``key`` (the reaction, e.g. an emoji), plus the optional ``ts`` (origin_server_ts in ms).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref, used to resolve ``sender``.
            rooms (dict[str, str]): The scenario's provisioned rooms (ref -> room_id), used to resolve ``room``.
            events (dict[str, str]): The events already provisioned in this scenario (ref -> event_id), used to resolve ``event``.
            txn_id (str): Unique to this deployment step; makes a repeated send return the original.

        Returns:
            JsonDict: The created reaction's ``event_id``.

        Raises:
            SynapseError: If a ref is missing/unknown or ``key`` is absent.
        """
        room_id = resolve_room_ref(spec.get("room"), rooms, "reaction room")
        sender = resolve_user_ref(spec.get("sender"), users, "reaction sender")
        target_event_id = resolve_event_ref(spec.get("event"), events, "reaction event")

        key = spec.get("key")
        if not key:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST, "reaction key is required", Codes.MISSING_PARAM
            )

        event_dict: JsonDict = {
            "type": EventTypes.Reaction,
            "room_id": room_id,
            "sender": sender,
            "content": {
                "m.relates_to": {
                    "rel_type": RelationTypes.ANNOTATION,
                    "event_id": target_event_id,
                    "key": key,
                }
            },
        }
        ts = spec.get("ts")
        if ts is not None:
            event_dict["origin_server_ts"] = ts

        event = await send_once(self.event_creation_handler, sender, event_dict, txn_id)
        return {"event_id": event.event_id}
