"""Provisions rooms, each owned by the run that created it."""

import json
import logging

from synapse.api.constants import EventTypes
from synapse.types import JsonDict, create_requester

from connect.fixtures.markers import CREATION_MARKER, RUN_MARKER, run_marker_content
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

    Creating a room is not atomic: a restart can cut it off after the create
    event but before the ownership state event. So the create event itself
    carries a ``CREATION_MARKER`` naming the deployment and ref, and
    ``created_by`` finds such a room even when it is half-made.
    """

    def __init__(self, hs):
        self.hs = hs
        self.store = hs.get_datastores().main
        self.room_creation_handler = hs.get_room_creation_handler()

    async def provision(
        self,
        spec: JsonDict,
        users: dict[str, JsonDict],
        run_id: str,
        deployment_id: str,
    ) -> JsonDict:
        """Create the room described by ``spec`` and return its ref and id.

        Args:
            spec (JsonDict): The room spec — ``ref``, ``creator`` (a user ref), plus the optional ``kind`` ("private" default / "public" / "dm"), ``name``, ``topic`` and ``admins`` (user refs given power level 100).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref, used to resolve ``creator``.
            run_id (str): The owning run; stamped on the room as the run marker.
            deployment_id (str): The deployment creating it; stamped on the room's create event and its run marker.

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
                    "content": run_marker_content(run_id, deployment_id),
                }
            ],
            "creation_content": {
                CREATION_MARKER: {
                    "deployment_id": deployment_id,
                    "ref": spec.get("ref"),
                }
            },
        }
        if spec.get("kind") == "dm":
            # Connect registers a people conversation (and the creator's
            # `m.direct`) only when createRoom carries both `is_direct` and
            # `invite`, so a DM invites its members here. Synapse sends those
            # invites with `is_direct`, which RoomMembershipListener reads; the
            # membership provisioner then only joins them.
            config["is_direct"] = True
            config["invite"] = self._dm_invitees(spec, users, creator)
        name = spec.get("name")
        if name:
            config["name"] = name
        topic = spec.get("topic")
        if topic:
            config["topic"] = topic
        admins = spec.get("admins")
        if admins:
            # Connect treats a non-bot user at power level 100 as a room admin
            # (`audiences_auth`). Synapse replaces `users` wholesale when it is
            # overridden, so the creator is listed too or would lose their 100.
            admin_ids = [resolve_user_ref(ref, users, "admin") for ref in admins]
            config["power_level_content_override"] = {
                "users": dict.fromkeys([creator, *admin_ids], 100)
            }

        room_id, _alias, _stream = await self.room_creation_handler.create_room(
            create_requester(creator), config, ratelimit=False
        )
        logger.info("fixtures provisioned room %s (run=%s)", room_id, run_id)
        return {"ref": spec.get("ref"), "room_id": room_id}

    async def created_by(self, creator: str, deployment_id: str, ref) -> list[str]:
        """The rooms ``creator`` created for ``deployment_id``'s room ``ref``.

        Finds a room even if its creation was cut short, so a retried room step
        can clear it before creating the room again.

        Args:
            creator (str): The room's creator.
            deployment_id (str): The deployment that created it.
            ref: The room's scenario-local ref.

        Returns:
            list[str]: Their room ids (normally none, at most one).
        """
        return await self.store.db_pool.runInteraction(
            "fixtures_select_created_rooms",
            self._created_by_txn,
            creator,
            {"deployment_id": deployment_id, "ref": ref},
        )

    @staticmethod
    def _created_by_txn(txn, creator: str, marker: JsonDict) -> list[str]:
        # Narrowed by creator first (a fixtures user creates few rooms), then
        # matched on the create event's content in Python, which keeps the SQL
        # portable.
        txn.execute(
            "SELECT r.room_id, j.json FROM rooms r "
            "JOIN current_state_events c "
            "ON c.room_id = r.room_id AND c.type = ? AND c.state_key = '' "
            "JOIN event_json j ON j.event_id = c.event_id "
            "WHERE r.creator = ?",
            (EventTypes.Create, creator),
        )
        return [
            room_id
            for room_id, event_json in txn
            if json.loads(event_json).get("content", {}).get(CREATION_MARKER) == marker
        ]

    @staticmethod
    def _dm_invitees(
        spec: JsonDict, users: dict[str, JsonDict], creator: str
    ) -> list[str]:
        # Everyone the DM starts with besides its creator: `members` and the
        # `left` users who join before parting.
        refs = dict.fromkeys([*spec.get("members", []), *spec.get("left", [])])
        invitees = [resolve_user_ref(ref, users, "member") for ref in refs]
        return [mxid for mxid in dict.fromkeys(invitees) if mxid != creator]
