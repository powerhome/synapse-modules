"""Tests for the audiences directory enricher."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from fixtures.directory import DirectoryEnricher


def make_enricher(base_url="http://audiences:3000"):
    hs = MagicMock()
    client = MagicMock()
    client.put_json = AsyncMock(return_value={})
    hs.get_simple_http_client.return_value = client
    return DirectoryEnricher(hs, base_url), client


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

    async def test_trailing_slash_in_base_url_is_normalized(self):
        enricher, client = make_enricher("http://audiences:3000/")

        await enricher.enrich({}, "carol", {})

        url, _body = client.put_json.await_args.args
        self.assertEqual(url, "http://audiences:3000/audiences/api/external_users")


if __name__ == "__main__":
    unittest.main()
