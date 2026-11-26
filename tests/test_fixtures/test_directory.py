"""Tests for the audiences directory enricher."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from fixtures.directory import DirectoryEnricher


def make_enricher(base_url="http://audiences:3000", access_group=""):
    hs = MagicMock()
    client = MagicMock()
    client.put_json = AsyncMock(return_value={})
    hs.get_simple_http_client.return_value = client
    return DirectoryEnricher(hs, base_url, access_group), client


ACCESS_GROUP = {
    "resource_type": "Generics",
    "value": "300001",
    "display": "Connect Access Group",
}


class DirectoryEnricherTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_puts_the_external_user_to_the_audiences_endpoint(self):
        enricher, client = make_enricher()

        await enricher.enrich(
            {"display_name": "Bob Smith"},
            "bob.smith",
            {"groups": ["Departments:BT"], "external_id": "bob.smith"},
        )

        url, body = client.put_json.await_args.args
        self.assertEqual(url, "http://audiences:3000/audiences/api/external_users")
        self.assertEqual(body["external_id"], "bob.smith")
        self.assertEqual(body["user_name"], "bob.smith")
        self.assertEqual(body["display_name"], "Bob Smith")
        self.assertEqual(
            body["groups"],
            [{"resource_type": "Departments", "value": "BT", "display": "BT"}],
        )

    async def test_external_id_defaults_to_the_localpart(self):
        enricher, client = make_enricher()

        await enricher.enrich({}, "carol", {"groups": []})

        _url, body = client.put_json.await_args.args
        self.assertEqual(body["external_id"], "carol")
        self.assertEqual(body["display_name"], "carol")

    async def test_a_bare_group_token_uses_itself_as_the_value(self):
        enricher, client = make_enricher()

        await enricher.enrich({}, "carol", {"groups": ["Roles"]})

        _url, body = client.put_json.await_args.args
        self.assertEqual(
            body["groups"],
            [{"resource_type": "Roles", "value": "Roles", "display": "Roles"}],
        )

    async def test_adds_the_connect_access_group_when_the_tenant_is_gated(self):
        enricher, client = make_enricher(access_group="300001")

        await enricher.enrich({}, "carol", {"groups": ["Departments:BT"]})

        _url, body = client.put_json.await_args.args
        self.assertEqual(
            body["groups"],
            [
                {"resource_type": "Departments", "value": "BT", "display": "BT"},
                ACCESS_GROUP,
            ],
        )

    async def test_does_not_add_the_access_group_twice(self):
        enricher, client = make_enricher(access_group="300001")

        await enricher.enrich({}, "carol", {"groups": ["Generics:300001"]})

        _url, body = client.put_json.await_args.args
        values = [
            g["value"] for g in body["groups"] if g["resource_type"] == "Generics"
        ]
        self.assertEqual(values, ["300001"])

    async def test_connect_access_false_leaves_the_user_without_the_group(self):
        enricher, client = make_enricher(access_group="300001")

        await enricher.enrich({}, "carol", {"connect_access": False})

        _url, body = client.put_json.await_args.args
        self.assertEqual(body["groups"], [])

    async def test_adds_no_access_group_on_an_ungated_tenant(self):
        enricher, client = make_enricher(access_group="")

        await enricher.enrich({}, "carol", {})

        _url, body = client.put_json.await_args.args
        self.assertEqual(body["groups"], [])

    async def test_trailing_slash_in_base_url_is_normalized(self):
        enricher, client = make_enricher("http://audiences:3000/")

        await enricher.enrich({}, "carol", {})

        url, _body = client.put_json.await_args.args
        self.assertEqual(url, "http://audiences:3000/audiences/api/external_users")


if __name__ == "__main__":
    unittest.main()
