"""Gives a seeded room an audience context (criteria + extra users) in CPG."""

import logging
from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict

from connect.fixtures.directory import DirectoryEnricher

logger = logging.getLogger(__name__)


class AudienceProvisioner:
    """Registers a room with CPG and sets its audience context.

    A room's audience (criteria such as ``Territories:PHL``, plus individually
    named extra users) lives in the audiences service (CPG), which is the sole
    arbiter of that room's membership. This makes the same two CPG calls the
    Synapse audience servlets proxy for a v3 client (which add power-level
    checks that seeding skips): ``POST /audiences/api/rooms`` for the context
    key, then ``PUT /audiences/api/contexts/{key}``. CPG then converges
    membership asynchronously (a Sidekiq job), inviting users the criteria
    match.

    Because CPG evicts anyone its context does not name, the room's creator and
    ``members`` are always added as extra users, so seeded members are not
    evicted; each therefore needs a linked ``external_id`` (a ``directory``
    block). Rooms created by the Synapse room-creation listener are already
    registered with CPG; the context PUT replaces their extra users, which is why
    the creator is re-added here.
    """

    def __init__(self, hs, base_url: str | None):
        self.client = hs.get_simple_http_client()
        self._base = base_url.rstrip("/") if base_url else None

    def validate(
        self,
        spec: JsonDict,
        users: dict[str, JsonDict],
        departed_refs: set = frozenset(),
    ) -> None:
        """Reject a room spec whose ``audience`` cannot be applied, before any seeding.

        Args:
            spec (JsonDict): The room spec.
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref.
            departed_refs (set): Refs of users the scenario deactivates.

        Raises:
            SynapseError: If the audiences service is not configured, the room is also ``archived``, or a user to be kept in the room is unknown or has no ``external_id``.
        """
        audience = spec.get("audience")
        if audience is None:
            return
        if not self._base:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "room audiences are not configured for this environment",
                Codes.UNKNOWN,
            )
        if spec.get("archived"):
            # CPG converges after the archive and would keep inviting the
            # context's users into the blocked room.
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "a room cannot have both an audience and archived",
                Codes.INVALID_PARAM,
            )
        self._extra_external_ids(spec, audience, users, departed_refs)

    async def provision(
        self,
        spec: JsonDict,
        users: dict[str, JsonDict],
        room_id: str,
        departed_refs: set = frozenset(),
    ) -> None:
        """Apply the room spec's ``audience`` to ``room_id`` in CPG, if it has one.

        Re-checks the spec with ``validate``, so it raises as that does.

        Args:
            spec (JsonDict): The room spec; its optional ``audience`` block carries ``criteria`` (a list of ``{"groups": ["ResourceType:value", ...]}``), ``extra_users`` (user refs) and ``match_all``.
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref, used to resolve each extra user's ``external_id``.
            room_id (str): The room to give an audience.
            departed_refs (set): Refs of users the scenario deactivates; with the room's ``left`` refs they are left out of the context, or CPG would re-invite them.
        """
        audience = spec.get("audience")
        if audience is None:
            return
        self.validate(spec, users, departed_refs)

        context = {
            "criteria": [
                {"groups": self._groups(criterion.get("groups", []))}
                for criterion in audience.get("criteria", [])
            ],
            "extra_users": [
                {"externalId": external_id}
                for external_id in self._extra_external_ids(
                    spec, audience, users, departed_refs
                )
            ],
            "match_all": bool(audience.get("match_all", False)),
        }

        room = await self.client.post_json_get_json(
            f"{self._base}/audiences/api/rooms", {"room": {"mxid": room_id}}
        )
        await self.client.put_json(
            f"{self._base}/audiences/api/contexts/{room['audiences_key']}", context
        )
        logger.info("fixtures set audience on room %s", room_id)

    @staticmethod
    def _groups(shorthands: list[str]) -> dict[str, list[JsonDict]]:
        # Group "ResourceType:value" shorthands by type; the value is the group's
        # external_id, as the directory enricher registers it.
        groups: dict[str, list[JsonDict]] = {}
        for shorthand in shorthands:
            parsed = DirectoryEnricher.parse_group(shorthand)
            groups.setdefault(parsed["resource_type"], []).append(
                {"externalId": parsed["value"]}
            )
        return groups

    @staticmethod
    def _extra_external_ids(
        spec: JsonDict,
        audience: JsonDict,
        users: dict[str, JsonDict],
        departed_refs: set,
    ) -> list[str]:
        refs = [
            spec.get("creator"),
            *spec.get("members", []),
            *audience.get("extra_users", []),
        ]
        # A user who leaves the room or is deactivated must not be named, or
        # CPG's later convergence re-invites them and undoes the departure.
        departing = {*spec.get("left", []), *departed_refs}
        external_ids = []
        for ref in dict.fromkeys(refs):
            if ref in departing:
                continue
            user = users.get(ref)
            if user is None:
                raise SynapseError(
                    HTTPStatus.BAD_REQUEST,
                    f"audience user '{ref}' is not a provisioned user in this scenario",
                    Codes.INVALID_PARAM,
                )
            if not user.get("external_id"):
                raise SynapseError(
                    HTTPStatus.BAD_REQUEST,
                    f"audience room user '{ref}' needs a directory block: CPG "
                    "manages the room's membership by external_id",
                    Codes.INVALID_PARAM,
                )
            external_ids.append(user["external_id"])
        return external_ids
