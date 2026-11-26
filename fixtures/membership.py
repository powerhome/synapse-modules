"""Seeds room memberships through the membership handler (not the servlet)."""

import logging

from synapse.api.constants import Membership
from synapse.types import JsonDict, UserID, create_requester

from connect.fixtures.refs import resolve_user_ref

logger = logging.getLogger(__name__)


class MembershipProvisioner:
    """Seeds a room's memberships in-process via the room-member handler.

    Going through ``room_member_handler.update_membership`` (not the HTTP
    servlet) is what lets membership be seeded despite the connect join-block,
    which lives in the servlet layer. Each member is invited by the room's
    creator and then joins, so the join is authorized by Matrix's own
    invite-then-join room auth rules. A room can also part users after the
    scenario's events (``left``). There is deliberately no "pending invite" state:
    ``synapse_auto_accept_invite`` joins every invitee immediately in every Connect
    environment, so a user is never left holding one. Memberships carry no marker of their own: a membership is owned by its room,
    so purging the room (run teardown) removes it.

    Seeding is safe to repeat after a restart: each change is skipped when the
    user already has the membership it would make (Synapse rejects inviting a
    user who has already joined).
    """

    def __init__(self, hs):
        self.hs = hs
        self.store = hs.get_datastores().main
        self.member_handler = hs.get_room_member_handler()

    async def provision(
        self, spec: JsonDict, users: dict[str, JsonDict], room_id: str
    ) -> None:
        """Seed the room's memberships: join ``members`` and ``left``.

        ``left`` users are joined here (so they can send events) and only parted
        later by ``leave``, once the scenario's events exist.

        Args:
            spec (JsonDict): The room spec, carrying ``creator`` (the inviter) and the optional ``members`` (user refs to join) and ``left`` (user refs who join, then part via ``leave``).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref.
            room_id (str): The room the members are seeded into.
        """
        creator = resolve_user_ref(spec.get("creator"), users, "creator")
        # A DM's members were already invited by createRoom (see
        # `RoomProvisioner`), and auto-accept may have joined them, so they are
        # only joined here.
        invited_at_creation = spec.get("kind") == "dm"

        # De-dupe refs so a user listed twice isn't invited/joined twice.
        joined_refs = dict.fromkeys([*spec.get("members", []), *spec.get("left", [])])
        for ref in joined_refs:
            member = resolve_user_ref(ref, users, "member")
            if member == creator:
                continue
            membership = await self._membership(room_id, member)
            if membership == Membership.JOIN:
                continue
            if not invited_at_creation and membership != Membership.INVITE:
                await self._invite(room_id, creator, member)
            await self._join(room_id, member)
            logger.info("fixtures joined %s to room %s", member, room_id)

    async def leave(
        self, spec: JsonDict, users: dict[str, JsonDict], room_id: str
    ) -> None:
        """Part each of the room's ``left`` users, as themselves.

        Runs after the scenario's events so a departed member can have posted
        before leaving — their history stays in the room.

        Args:
            spec (JsonDict): The room spec, carrying the optional ``left`` (a list of user refs).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref.
            room_id (str): The room the users leave.
        """
        for ref in dict.fromkeys(spec.get("left", [])):
            member = resolve_user_ref(ref, users, "left")
            if await self._membership(room_id, member) != Membership.JOIN:
                continue
            await self.member_handler.update_membership(
                requester=create_requester(member),
                target=UserID.from_string(member),
                room_id=room_id,
                action=Membership.LEAVE,
                ratelimit=False,
            )
            logger.info("fixtures parted %s from room %s", member, room_id)

    async def _membership(self, room_id: str, member: str) -> str | None:
        (
            membership,
            _event_id,
        ) = await self.store.get_local_current_membership_for_user_in_room(
            member, room_id
        )
        return membership

    async def _invite(self, room_id: str, creator: str, member: str) -> None:
        # Invite and join both go through the handler, so the connect servlet
        # join-block (servlet-layer policy) is bypassed while Matrix's
        # invite-then-join auth rules are still honored.
        await self.member_handler.update_membership(
            requester=create_requester(creator),
            target=UserID.from_string(member),
            room_id=room_id,
            action=Membership.INVITE,
            ratelimit=False,
        )

    async def _join(self, room_id: str, member: str) -> None:
        await self.member_handler.update_membership(
            requester=create_requester(member),
            target=UserID.from_string(member),
            room_id=room_id,
            action=Membership.JOIN,
            ratelimit=False,
        )
