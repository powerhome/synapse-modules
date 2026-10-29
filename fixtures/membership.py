"""Seeds room memberships through the membership handler (not the servlet)."""

import logging

from synapse.api.constants import Membership
from synapse.types import JsonDict, UserID, create_requester

from connect.fixtures.refs import resolve_user_ref

logger = logging.getLogger(__name__)


class MembershipProvisioner:
    """Joins a room's members in-process via the room-member handler.

    Going through ``room_member_handler.update_membership`` (not the HTTP
    servlet) is what lets membership be seeded despite the connect join-block,
    which lives in the servlet layer. Each member is invited by the room's
    creator and then joins, so the join is authorized by Matrix's own
    invite-then-join room auth rules. Members carry no marker of their own: a
    membership is owned by its room, so purging the room (run teardown) removes
    it.
    """

    def __init__(self, hs):
        self.hs = hs
        self.member_handler = hs.get_room_member_handler()

    async def provision(
        self, spec: JsonDict, users: dict[str, JsonDict], room_id: str
    ) -> None:
        """Invite and join each of the room's ``members``.

        Args:
            spec (JsonDict): The room spec, carrying ``creator`` (the inviter) and the optional ``members`` (a list of user refs).
            users (dict[str, JsonDict]): The scenario's provisioned users keyed by ref.
            room_id (str): The room the members are joined to.
        """
        creator = resolve_user_ref(spec.get("creator"), users, "creator")
        # De-dupe refs so a member listed twice isn't invited/joined twice.
        for ref in dict.fromkeys(spec.get("members", [])):
            member = resolve_user_ref(ref, users, "member")
            if member == creator:
                continue
            await self._invite_and_join(room_id, creator, member)
            logger.info("fixtures joined %s to room %s", member, room_id)

    async def _invite_and_join(self, room_id: str, creator: str, member: str) -> None:
        # Creator invites, then the member joins — both through the handler, so
        # the connect servlet join-block (servlet-layer policy) is bypassed while
        # Matrix's invite-then-join auth rules are still honored.
        target = UserID.from_string(member)
        await self.member_handler.update_membership(
            requester=create_requester(creator),
            target=target,
            room_id=room_id,
            action=Membership.INVITE,
            ratelimit=False,
        )
        await self.member_handler.update_membership(
            requester=create_requester(member),
            target=target,
            room_id=room_id,
            action=Membership.JOIN,
            ratelimit=False,
        )
