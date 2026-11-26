"""Tests for the connect.profiles writer."""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from fixtures.profiles import SCIM_EXTENSION, ProfileWriter


def make_writer():
    hs = MagicMock()
    db_pool = hs.get_datastores.return_value.main.db_pool
    db_pool.simple_upsert = AsyncMock(return_value=True)
    db_pool.simple_update = AsyncMock(return_value=1)
    db_pool.simple_delete = AsyncMock(return_value=1)
    return ProfileWriter(hs), db_pool


def upserted(db_pool):
    kwargs = db_pool.simple_upsert.await_args.kwargs
    return kwargs, json.loads(kwargs["values"]["data"])


class WriteTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_upserts_an_active_row_keyed_by_localpart(self):
        writer, db_pool = make_writer()

        await writer.write("alice", {"job_title": "Foreman"}, external_id=None)

        kwargs, _ = upserted(db_pool)
        self.assertEqual(kwargs["table"], "connect.profiles")
        self.assertEqual(kwargs["keyvalues"], {"user_id": "alice"})
        self.assertIs(kwargs["values"]["active"], True)

    async def test_maps_directory_fields_to_the_scim_shape_the_queries_read(self):
        writer, db_pool = make_writer()

        await writer.write(
            "alice",
            {
                "job_title": "Foreman",
                "department": "Installation",
                "territory": "Philadelphia",
                "territory_abbreviation": "PHL",
            },
            external_id=None,
        )

        _, data = upserted(db_pool)
        self.assertEqual(data["title"], "Foreman")
        self.assertEqual(
            data[SCIM_EXTENSION],
            {
                "department": "Installation",
                "territory": "Philadelphia",
                "territoryAbbr": "PHL",
            },
        )

    async def test_records_the_external_id_when_the_user_has_one(self):
        writer, db_pool = make_writer()

        await writer.write("alice", {}, external_id="1234")

        _, data = upserted(db_pool)
        self.assertEqual(data["externalId"], "1234")

    async def test_omits_fields_the_spec_does_not_set(self):
        writer, db_pool = make_writer()

        await writer.write("alice", {}, external_id=None)

        _, data = upserted(db_pool)
        self.assertEqual(data, {})


class DeactivateTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_marks_the_profile_inactive_like_the_populator_does(self):
        writer, db_pool = make_writer()

        await writer.deactivate("alice")

        kwargs = db_pool.simple_update.await_args.kwargs
        self.assertEqual(kwargs["table"], "connect.profiles")
        self.assertEqual(kwargs["keyvalues"], {"user_id": "alice"})
        self.assertEqual(kwargs["updatevalues"], {"active": False})


class RemoveTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_deletes_each_localparts_row(self):
        writer, db_pool = make_writer()

        await writer.remove(["alice", "bob"])

        deleted = [c.kwargs["keyvalues"] for c in db_pool.simple_delete.await_args_list]
        self.assertEqual(deleted, [{"user_id": "alice"}, {"user_id": "bob"}])
        self.assertEqual(
            db_pool.simple_delete.await_args.kwargs["table"], "connect.profiles"
        )


if __name__ == "__main__":
    unittest.main()
