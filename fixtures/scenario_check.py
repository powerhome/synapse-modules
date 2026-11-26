"""Up-front checks on a scenario, run before any resource is created."""

from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict

from connect.fixtures.refs import resolve_event_ref, resolve_room_ref, resolve_user_ref

# The scenario lists the provisioner walks, each one a resource kind.
RESOURCE_KINDS = (
    "users",
    "rooms",
    "events",
    "media",
    "reactions",
    "redactions",
    "receipts",
)


def resource_count(scenario: JsonDict) -> int:
    """How many resources ``scenario`` provisions, memberships included.

    Args:
        scenario (JsonDict): The scenario request.

    Returns:
        int: The scenario's size, as the resource caps measure it.
    """
    memberships = sum(
        len(room.get("members", [])) + len(room.get("left", []))
        for room in scenario.get("rooms", [])
    )
    listed = sum(len(scenario.get(kind, [])) for kind in RESOURCE_KINDS)
    return listed + memberships


def check_size(scenario: JsonDict, limit: int, hint: str = "") -> None:
    """Reject a scenario larger than ``limit`` resources.

    Args:
        scenario (JsonDict): The scenario request.
        limit (int): The most resources the caller's endpoint accepts.
        hint (str): Appended to the error, e.g. where a larger scenario can go.

    Raises:
        SynapseError: 413 if the scenario is over the limit.
    """
    count = resource_count(scenario)
    if count > limit:
        raise SynapseError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            f"scenario has {count} resources, over the limit of {limit}{hint}",
            Codes.TOO_LARGE,
        )


def check_refs(scenario: JsonDict) -> None:
    """Reject a scenario naming a ref it does not declare, before any work.

    Resolves every ref-bearing field against the refs the scenario declares,
    in the order the provisioner creates them, using the same resolvers (and so
    the same 400s) the provisioners use mid-run. A reply or edit can only name
    an earlier event; a reaction, redaction or receipt can name any event or
    media item. Every one of them must name an event in its own room, as
    Synapse refuses a relation or redaction across rooms.

    Args:
        scenario (JsonDict): The scenario request.

    Raises:
        SynapseError: 400 on a missing or undeclared ref.
    """  # noqa: DAR402
    # Placeholder tables shaped like the provisioned ones, keyed by declared ref.
    users = {
        spec.get("ref"): {"mxid": spec.get("ref")} for spec in _specs(scenario, "users")
    }
    rooms = {spec.get("ref"): spec.get("ref") for spec in _specs(scenario, "rooms")}
    # Event ref -> the room ref it is sent into.
    events: dict[str, str] = {}

    for spec in _specs(scenario, "rooms"):
        resolve_user_ref(spec.get("creator"), users, "creator")
        for ref in [*spec.get("members", []), *spec.get("left", [])]:
            resolve_user_ref(ref, users, "member")
        for ref in spec.get("admins") or []:
            resolve_user_ref(ref, users, "admin")

    for spec in _specs(scenario, "events"):
        _check_message(spec, users, rooms, "event")
        for ref in spec.get("mentions") or []:
            resolve_user_ref(ref, users, "event mentions")
        for field in ("reply_to", "edit_of"):
            if spec.get(field):
                _check_same_room(spec, spec[field], events, f"event {field}")
        _declare_event(spec, events)

    for spec in _specs(scenario, "media"):
        _check_message(spec, users, rooms, "media")
        _declare_event(spec, events)

    for kind, noun in (("reactions", "reaction"), ("redactions", "redaction")):
        for spec in _specs(scenario, kind):
            _check_message(spec, users, rooms, noun)
            _check_same_room(spec, spec.get("event"), events, f"{noun} event")

    for spec in _specs(scenario, "receipts"):
        resolve_room_ref(spec.get("room"), rooms, "receipt room")
        resolve_user_ref(spec.get("user"), users, "receipt user")
        _check_same_room(spec, spec.get("event"), events, "receipt event")


def _specs(scenario: JsonDict, kind: str) -> list[JsonDict]:
    return scenario.get(kind, [])


def _check_message(spec: JsonDict, users: dict, rooms: dict, noun: str) -> None:
    # Every timeline resource is sent into a room by a member.
    resolve_room_ref(spec.get("room"), rooms, f"{noun} room")
    resolve_user_ref(spec.get("sender"), users, f"{noun} sender")


def _declare_event(spec: JsonDict, events: dict[str, str]) -> None:
    # An event or media item with a ref becomes nameable by later resources.
    if spec.get("ref"):
        events[spec["ref"]] = spec.get("room")


def _check_same_room(spec: JsonDict, ref, events: dict[str, str], field: str) -> None:
    # `ref` must be a declared event, sent into the same room as `spec`.
    event_room = resolve_event_ref(ref, events, field)
    if event_room != spec.get("room"):
        raise SynapseError(
            HTTPStatus.BAD_REQUEST,
            f"{field} '{ref}' is in room '{event_room}', not '{spec.get('room')}'",
            Codes.INVALID_PARAM,
        )
