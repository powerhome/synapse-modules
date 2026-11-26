"""Tests for the membership provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.constants import Membership
from synapse.api.errors import SynapseError

from fixtures.membership import MembershipProvisioner


def make_provisioner(memberships=None):
    # `memberships` maps an mxid to its current membership in the room.
    memberships = memberships or {}
    hs = MagicMock()
    member_handler = MagicMock()
    member_handler.update_membership = AsyncMock(return_value=("$evt", 1))
    hs.get_room_member_handler.return_value = member_handler
    store = MagicMock()
    store.get_local_current_membership_for_user_in_room = AsyncMock(
        side_effect=lambda user_id, room_id: (memberships.get(user_id), None)
    )
    hs.get_datastores.return_value.main = store
    return MembershipProvisioner(hs), member_handler


USERS = {
    "alice": {"mxid": "@alice:localhost"},
    "bob": {"mxid": "@bob:localhost"},
}


class ProvisionTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_invites_then_joins_each_member(self):
        provisioner, member_handler = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "members": ["bob"]}, USERS, "!room:localhost"
        )

        self.assertEqual(member_handler.update_membership.await_count, 2)
        invite, join = member_handler.update_membership.await_args_list
        self.assertEqual(invite.kwargs["action"], Membership.INVITE)
        self.assertEqual(invite.kwargs["target"].to_string(), "@bob:localhost")
        self.assertEqual(join.kwargs["action"], Membership.JOIN)
        self.assertEqual(join.kwargs["target"].to_string(), "@bob:localhost")

    async def test_dm_members_are_joined_without_a_second_invite(self):
        provisioner, member_handler = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "kind": "dm", "members": ["bob"]},
            USERS,
            "!room:localhost",
        )

        # createRoom already invited bob (with `is_direct`); he only joins.
        (join,) = member_handler.update_membership.await_args_list
        self.assertEqual(join.kwargs["action"], Membership.JOIN)
        self.assertEqual(join.kwargs["target"].to_string(), "@bob:localhost")

    async def test_non_dm_invites_carry_no_extra_content(self):
        provisioner, member_handler = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "members": ["bob"]}, USERS, "!room:localhost"
        )

        invite, _ = member_handler.update_membership.await_args_list
        self.assertNotIn("content", invite.kwargs)

    async def test_skips_the_creator_already_joined_on_create(self):
        provisioner, member_handler = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "members": ["alice", "bob"]},
            USERS,
            "!room:localhost",
        )

        # Only bob is invited+joined; alice (creator) is skipped.
        self.assertEqual(member_handler.update_membership.await_count, 2)

    async def test_no_members_does_nothing(self):
        provisioner, member_handler = make_provisioner()

        await provisioner.provision({"creator": "alice"}, USERS, "!room:localhost")

        member_handler.update_membership.assert_not_awaited()

    async def test_duplicate_member_refs_are_invited_and_joined_once(self):
        provisioner, member_handler = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "members": ["bob", "bob"]},
            USERS,
            "!room:localhost",
        )

        self.assertEqual(member_handler.update_membership.await_count, 2)

    async def test_left_users_are_joined_so_they_can_act_before_leaving(self):
        provisioner, member_handler = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "left": ["bob"]}, USERS, "!room:localhost"
        )

        invite, join = member_handler.update_membership.await_args_list
        self.assertEqual(invite.kwargs["action"], Membership.INVITE)
        self.assertEqual(join.kwargs["action"], Membership.JOIN)
        self.assertEqual(join.kwargs["target"].to_string(), "@bob:localhost")

    async def test_leave_parts_each_left_user_as_themselves(self):
        provisioner, member_handler = make_provisioner({"@bob:localhost": "join"})

        await provisioner.leave(
            {"creator": "alice", "left": ["bob"]}, USERS, "!room:localhost"
        )

        member_handler.update_membership.assert_awaited_once()
        leave = member_handler.update_membership.await_args
        self.assertEqual(leave.kwargs["action"], Membership.LEAVE)
        self.assertEqual(leave.kwargs["requester"].user.to_string(), "@bob:localhost")
        self.assertEqual(leave.kwargs["target"].to_string(), "@bob:localhost")
        self.assertFalse(leave.kwargs["ratelimit"])

    async def test_leave_with_no_left_users_does_nothing(self):
        provisioner, member_handler = make_provisioner()

        await provisioner.leave(
            {"creator": "alice", "members": ["bob"]}, USERS, "!room:localhost"
        )

        member_handler.update_membership.assert_not_awaited()

    async def test_an_already_joined_member_is_neither_invited_nor_joined(self):
        provisioner, member_handler = make_provisioner({"@bob:localhost": "join"})

        await provisioner.provision(
            {"creator": "alice", "members": ["bob"]}, USERS, "!room:localhost"
        )

        member_handler.update_membership.assert_not_awaited()

    async def test_an_invited_member_is_only_joined(self):
        provisioner, member_handler = make_provisioner({"@bob:localhost": "invite"})

        await provisioner.provision(
            {"creator": "alice", "members": ["bob"]}, USERS, "!room:localhost"
        )

        join = member_handler.update_membership.await_args
        self.assertEqual(member_handler.update_membership.await_count, 1)
        self.assertEqual(join.kwargs["action"], Membership.JOIN)

    async def test_leave_skips_a_user_who_already_left(self):
        provisioner, member_handler = make_provisioner({"@bob:localhost": "leave"})

        await provisioner.leave({"left": ["bob"]}, USERS, "!room:localhost")

        member_handler.update_membership.assert_not_awaited()

    async def test_missing_creator_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision({"members": ["bob"]}, USERS, "!room:localhost")
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_unknown_member_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"creator": "alice", "members": ["ghost"]},
                USERS,
                "!room:localhost",
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
