"""Tests for the room audience provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import SynapseError

from fixtures.audience import AudienceProvisioner


def make_provisioner(base_url="http://audiences:3000/"):
    hs = MagicMock()
    client = MagicMock()
    client.post_json_get_json = AsyncMock(return_value={"audiences_key": "signed-key"})
    client.put_json = AsyncMock(return_value={})
    hs.get_simple_http_client.return_value = client
    return AudienceProvisioner(hs, base_url), client


USERS = {
    "alice": {"mxid": "@alice:localhost", "external_id": "100"},
    "bob": {"mxid": "@bob:localhost", "external_id": "200"},
    "carol": {"mxid": "@carol:localhost", "external_id": "300"},
    "plain": {"mxid": "@plain:localhost"},
}


def put_body(client):
    return client.put_json.await_args.args[1]


class ProvisionTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_registers_the_room_then_puts_its_context_under_the_returned_key(
        self,
    ):
        provisioner, client = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "audience": {}}, USERS, "!room:localhost"
        )

        post = client.post_json_get_json.await_args
        self.assertEqual(post.args[0], "http://audiences:3000/audiences/api/rooms")
        self.assertEqual(post.args[1], {"room": {"mxid": "!room:localhost"}})
        self.assertEqual(
            client.put_json.await_args.args[0],
            "http://audiences:3000/audiences/api/contexts/signed-key",
        )

    async def test_does_nothing_for_a_room_without_an_audience(self):
        provisioner, client = make_provisioner()

        await provisioner.provision({"creator": "alice"}, USERS, "!room:localhost")

        client.post_json_get_json.assert_not_awaited()
        client.put_json.assert_not_awaited()

    async def test_groups_criteria_by_resource_type(self):
        provisioner, client = make_provisioner()

        await provisioner.provision(
            {
                "creator": "alice",
                "audience": {
                    "criteria": [
                        {
                            "groups": [
                                "Territories:PHL",
                                "Territories:NYC",
                                "Titles:Foreman",
                            ]
                        }
                    ]
                },
            },
            USERS,
            "!room:localhost",
        )

        self.assertEqual(
            put_body(client)["criteria"],
            [
                {
                    "groups": {
                        "Territories": [{"externalId": "PHL"}, {"externalId": "NYC"}],
                        "Titles": [{"externalId": "Foreman"}],
                    }
                }
            ],
        )

    async def test_match_all_defaults_to_false(self):
        provisioner, client = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "audience": {}}, USERS, "!room:localhost"
        )

        self.assertIs(put_body(client)["match_all"], False)

    async def test_passes_match_all_through(self):
        provisioner, client = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "audience": {"match_all": True}},
            USERS,
            "!room:localhost",
        )

        self.assertIs(put_body(client)["match_all"], True)

    async def test_keeps_the_creator_and_members_as_extra_users(self):
        provisioner, client = make_provisioner()

        await provisioner.provision(
            {"creator": "alice", "members": ["bob"], "audience": {}},
            USERS,
            "!room:localhost",
        )

        # CPG arbitrates membership of an audience room, so anyone the scenario
        # put in it must be an extra user or CPG would evict them.
        self.assertEqual(
            put_body(client)["extra_users"],
            [{"externalId": "100"}, {"externalId": "200"}],
        )

    async def test_adds_the_explicit_extra_users_without_duplicating(self):
        provisioner, client = make_provisioner()

        await provisioner.provision(
            {
                "creator": "alice",
                "members": ["bob"],
                "audience": {"extra_users": ["carol", "bob"]},
            },
            USERS,
            "!room:localhost",
        )

        self.assertEqual(
            put_body(client)["extra_users"],
            [{"externalId": "100"}, {"externalId": "200"}, {"externalId": "300"}],
        )

    async def test_a_member_without_an_external_id_is_a_bad_request(self):
        provisioner, client = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"creator": "alice", "members": ["plain"], "audience": {}},
                USERS,
                "!room:localhost",
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        client.put_json.assert_not_awaited()

    async def test_leaves_departing_users_out_of_the_context(self):
        provisioner, client = make_provisioner()

        await provisioner.provision(
            {
                "creator": "alice",
                "members": ["bob", "carol"],
                "left": ["bob"],
                "audience": {},
            },
            USERS,
            "!room:localhost",
            {"carol"},
        )

        self.assertEqual(put_body(client)["extra_users"], [{"externalId": "100"}])

    async def test_an_archived_audience_room_is_a_bad_request(self):
        provisioner, client = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            provisioner.validate(
                {"creator": "alice", "archived": True, "audience": {}}, USERS
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_validate_rejects_a_member_without_an_external_id_without_calling_cpg(
        self,
    ):
        provisioner, client = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            provisioner.validate(
                {"creator": "alice", "members": ["plain"], "audience": {}}, USERS
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        client.post_json_get_json.assert_not_awaited()

    async def test_an_unknown_extra_user_ref_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"creator": "alice", "audience": {"extra_users": ["ghost"]}},
                USERS,
                "!room:localhost",
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_an_audience_without_a_configured_audiences_service_is_a_bad_request(
        self,
    ):
        provisioner, client = make_provisioner(base_url=None)

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"creator": "alice", "audience": {}}, USERS, "!room:localhost"
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        client.post_json_get_json.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
