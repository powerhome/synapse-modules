"""Resolves scenario-local ``refs`` (users, rooms) to their provisioned ids."""

from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict


def _resolve_ref(ref, table: dict, field: str, noun: str):
    """Look up a scenario-local ref in a provisioned-resource table.

    Shared by ``resolve_user_ref``/``resolve_event_ref``/``resolve_room_ref`` so
    every spec field that names an already-provisioned resource fails the same
    way — a clear ``400`` — when the ref is missing or unknown to the scenario.

    Args:
        ref: The scenario-local ref to resolve (may be falsy if omitted).
        table (dict): The scenario's provisioned resources, keyed by ref.
        field (str): The spec field being resolved, named in the error message.
        noun (str): The resource kind, named in the "not a provisioned ..." error.

    Returns:
        The resolved value from ``table``.

    Raises:
        SynapseError: If ``ref`` is missing or names a resource not in the scenario.
    """
    if not ref:
        raise SynapseError(
            HTTPStatus.BAD_REQUEST, f"{field} is required", Codes.MISSING_PARAM
        )
    value = table.get(ref)
    if not value:
        raise SynapseError(
            HTTPStatus.BAD_REQUEST,
            f"{field} '{ref}' is not a provisioned {noun} in this scenario",
            Codes.INVALID_PARAM,
        )
    return value


def resolve_user_ref(ref, users: dict[str, JsonDict], field: str) -> str:
    """Map a user ``ref`` to the mxid it was provisioned with.

    See ``_resolve_ref`` for the shared lookup/error behavior.

    Args:
        ref: The scenario-local user ref to resolve (may be falsy if omitted).
        users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref.
        field (str): The spec field being resolved, named in the error message.

    Returns:
        str: The resolved user's mxid.
    """
    return _resolve_ref(ref, users, field, "user")["mxid"]


def resolve_event_ref(ref, events: dict[str, str], field: str) -> str:
    """Map an event ``ref`` to the event id it was provisioned with.

    See ``_resolve_ref`` for the shared lookup/error behavior.

    Args:
        ref: The scenario-local event ref to resolve (may be falsy if omitted).
        events (dict[str, str]): The scenario's provisioned events (ref -> event_id).
        field (str): The spec field being resolved, named in the error message.

    Returns:
        str: The resolved event id.
    """
    return _resolve_ref(ref, events, field, "event")


def resolve_room_ref(ref, rooms: dict[str, str], field: str) -> str:
    """Map a room ``ref`` to the room id it was provisioned with.

    See ``_resolve_ref`` for the shared lookup/error behavior.

    Args:
        ref: The scenario-local room ref to resolve (may be falsy if omitted).
        rooms (dict[str, str]): The scenario's provisioned rooms (ref -> room_id).
        field (str): The spec field being resolved, named in the error message.

    Returns:
        str: The resolved room id.
    """
    return _resolve_ref(ref, rooms, field, "room")
