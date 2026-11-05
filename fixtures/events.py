"""Provisions timeline events (m.room.message) through the event handler."""

from http import HTTPStatus

from synapse.api.constants import EventTypes
from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict

from connect.fixtures.refs import resolve_event_ref, resolve_room_ref, resolve_user_ref
from connect.fixtures.sending import send_once


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
        *,
        txn_id: str,
    ) -> JsonDict:
        """Send the message described by ``spec`` and return its ref and id.

        Args:
            spec (JsonDict): The event spec — ``room`` (a room ref), ``sender`` (a user ref), ``body`` (the message text), plus the optional ``ref`` (so later events can ``reply_to`` it), ``ts`` (origin_server_ts in ms), ``reply_to`` (an event ref), ``edit_of`` (an event ref this message replaces; ``body`` is the new text), and ``mentions`` (a list of user refs to mark as mentioned via ``m.mentions``, so the fixture can drive Connect's mention-triggered push rules deterministically).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref, used to resolve ``sender``.
            rooms (dict[str, str]): The scenario's provisioned rooms (ref -> room_id), used to resolve ``room``.
            events (dict[str, str]): The events already provisioned in this scenario (ref -> event_id), used to resolve ``reply_to``.
            txn_id (str): Unique to this deployment step; makes a repeated send return the original.

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
        edit_of = spec.get("edit_of")
        if reply_to and edit_of:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "an event cannot both reply_to and edit_of another event",
                Codes.INVALID_PARAM,
            )
        if reply_to:
            replied_event_id = self._resolve_reply(reply_to, events)
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": replied_event_id}}
        if edit_of:
            content = self._edit_content(body, self._resolve_edit(edit_of, events))

        mentions = spec.get("mentions")
        if mentions:
            mentioned_ids = [
                resolve_user_ref(ref, users, "event mentions") for ref in mentions
            ]
            content["m.mentions"] = {"user_ids": mentioned_ids}
            if edit_of:
                # Clients and push rules read an edit's mentions from the
                # replacement content.
                content["m.new_content"]["m.mentions"] = {"user_ids": mentioned_ids}

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

        event = await send_once(self.event_creation_handler, sender, event_dict, txn_id)
        return {"ref": spec.get("ref"), "event_id": event.event_id}

    @staticmethod
    def _edit_content(body: str, edited_event_id: str) -> JsonDict:
        # A Matrix edit is a new message related to the original by `m.replace`:
        # the replacement text rides in `m.new_content`, and the fallback `body`
        # is "* "-prefixed for clients that don't understand edits.
        return {
            "msgtype": "m.text",
            "body": f"* {body}",
            "m.new_content": {"msgtype": "m.text", "body": body},
            "m.relates_to": {"rel_type": "m.replace", "event_id": edited_event_id},
        }

    @staticmethod
    def _resolve_edit(ref: str, events: dict[str, str]) -> str:
        # Like a reply, an edit can only name an event provisioned earlier in the
        # same scenario, so a forward/unknown ref is a 400.
        return resolve_event_ref(ref, events, "event edit_of")

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
