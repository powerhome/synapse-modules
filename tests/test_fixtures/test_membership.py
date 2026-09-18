"""Tests for the membership provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.constants import Membership
from synapse.api.errors import SynapseError

from fixtures.membership import MembershipProvisioner


def make_provisioner():
    hs = MagicMock()
    member_handler = MagicMock()
    member_handler.update_membership = AsyncMock(return_value=("$evt", 1))
    hs.get_room_member_handler.return_value = member_handler
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
