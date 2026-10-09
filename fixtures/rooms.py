"""Provisions rooms, each owned by the run that created it."""

import logging

from synapse.types import JsonDict, create_requester

from connect.fixtures.markers import RUN_MARKER, run_marker_content
from connect.fixtures.refs import resolve_user_ref

logger = logging.getLogger(__name__)


class RoomProvisioner:
    """Creates rooms through Synapse's room-creation handler.

    Each room is created by its scenario ``creator`` (a provisioned user) and
    stamped — atomically, in the room's initial state — with the ``RUN_MARKER``
    ownership state event, whose ``state_key`` is the owning ``run_id``. That
    makes run/global teardown a single ``current_state_events`` lookup, the room
    analog of the user's account-data run marker.

    Rooms have random ids and no caller-chosen name, so (unlike a ``Fixed`` user)
    there is nothing to collide on: a room is always created fresh and owned by
    exactly one run — the ``Random``-user case of the ownership model. Enforcing
    a name-uniqueness rule here would be artificial, so there is none.
    """

    def __init__(self, hs):
        self.hs = hs
        self.room_creation_handler = hs.get_room_creation_handler()

    async def provision(
        self, spec: JsonDict, users: dict[str, JsonDict], run_id: str
    ) -> JsonDict:
        """Create the room described by ``spec`` and return its ref and id.

        Args:
            spec (JsonDict): The room spec — ``ref``, ``creator`` (a user ref), plus the optional ``kind`` ("private" default / "public") and ``name``.
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref, used to resolve ``creator``.
            run_id (str): The owning run; stamped on the room as the run marker.

        Returns:
            JsonDict: ``ref`` and the created ``room_id``.
        """
        creator = resolve_user_ref(spec.get("creator"), users, "creator")

        is_public = spec.get("kind") == "public"
        config = {
            "preset": "public_chat" if is_public else "private_chat",
            # `preset` alone only sets join_rules — it does not publish the room
            # to the directory a "public rooms" browser queries. `visibility` is
            # the separate Synapse knob that does, so a `kind: "public"` room is
            # actually discoverable rather than merely joinable-if-known.
            "visibility": "public" if is_public else "private",
            "initial_state": [
                {
                    "type": RUN_MARKER,
                    "state_key": run_id,
                    "content": run_marker_content(run_id),
                }
            ],
        }
        name = spec.get("name")
        if name:
            config["name"] = name

        room_id, _alias, _stream = await self.room_creation_handler.create_room(
            create_requester(creator), config, ratelimit=False
        )
        logger.info("fixtures provisioned room %s (run=%s)", room_id, run_id)
        return {"ref": spec.get("ref"), "room_id": room_id}
