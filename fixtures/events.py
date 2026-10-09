"""Provisions timeline events (m.room.message) through the event handler."""

from http import HTTPStatus

from synapse.api.constants import EventTypes
from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict, create_requester

from connect.fixtures.refs import resolve_room_ref, resolve_user_ref


class EventProvisioner:
    """Sends messages into a provisioned room via the event-creation handler.

    Going through ``hs.get_event_creation_handler()`` (the same in-process path
    the membership provisioner uses) is what lets a timeline be seeded with real
    events the logged-in member then syncs down — the seam the CCL timeline E2E
    tier reads against (``observe_timeline`` / ``paginate_back``).

    A seeded timeline needs **pinned timestamps** so it can drive recency-ordering
    and pagination assertions deterministically: when a spec carries ``ts`` it is
    set as the event's ``origin_server_ts`` (honored by the event builder), and
    when it is omitted the server stamps wall-clock time as usual. Events carry no
    marker of their own — an event is owned by its room, so purging the room (run
    teardown) removes it, exactly like a membership.
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
    ) -> JsonDict:
        """Send the message described by ``spec`` and return its ref and id.

        Args:
            spec (JsonDict): The event spec — ``room`` (a room ref), ``sender`` (a user ref), ``body`` (the message text), plus the optional ``ref`` (so later events can ``reply_to`` it), ``ts`` (origin_server_ts in ms), ``reply_to`` (an event ref), and ``mentions`` (a list of user refs to mark as mentioned via ``m.mentions``, so the fixture can drive Connect's mention-triggered push rules deterministically).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref, used to resolve ``sender``.
            rooms (dict[str, str]): The scenario's provisioned rooms (ref -> room_id), used to resolve ``room``.
            events (dict[str, str]): The events already provisioned in this scenario (ref -> event_id), used to resolve ``reply_to``.

        Returns:
            JsonDict: ``ref`` (may be ``None``) and the created ``event_id``.

        Raises:
            SynapseError: If a ref is missing/unknown or ``body`` is absent.
        """
        room_id = resolve_room_ref(spec.get("room"), rooms, "event room")
        sender = resolve_user_ref(spec.get("sender"), users, "event sender")

        body = spec.get("body")
        if not body:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST, "event body is required", Codes.MISSING_PARAM
            )

        content: JsonDict = {"msgtype": "m.text", "body": body}
        reply_to = spec.get("reply_to")
        if reply_to:
            replied_event_id = self._resolve_reply(reply_to, events)
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": replied_event_id}}

        mentions = spec.get("mentions")
        if mentions:
            mentioned_ids = [
                resolve_user_ref(ref, users, "event mentions") for ref in mentions
            ]
            content["m.mentions"] = {"user_ids": mentioned_ids}

        event_dict: JsonDict = {
            "type": EventTypes.Message,
            "room_id": room_id,
            "sender": sender,
            "content": content,
        }
        # A pinned timestamp rides in the event dict; the builder picks it up via
        # `origin_server_ts`. Omitted -> the server stamps wall-clock time.
        ts = spec.get("ts")
        if ts is not None:
            event_dict["origin_server_ts"] = ts

        event, _ = await self.event_creation_handler.create_and_send_nonmember_event(
            create_requester(sender), event_dict, ratelimit=False
        )
        return {"ref": spec.get("ref"), "event_id": event.event_id}

    @staticmethod
    def _resolve_reply(ref: str, events: dict[str, str]) -> str:
        # An event can only reply to one already provisioned earlier in the same
        # scenario (resolution is ordered), so a forward/unknown ref is a 400.
        event_id = events.get(ref)
        if not event_id:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"event reply_to '{ref}' is not a provisioned event in this scenario",
                Codes.INVALID_PARAM,
            )
        return event_id
