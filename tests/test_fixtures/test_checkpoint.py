"""Tests for the deployment checkpoint."""

import unittest

from fixtures.checkpoint import Checkpoint, IncompatibleCheckpointError


class CheckpointTestSuite(unittest.TestCase):
    def test_round_trips_through_its_stored_form(self):
        checkpoint = Checkpoint(
            next_index=4,
            users={"a": {"mxid": "@a:hs"}},
            rooms={"r": "!r:hs"},
            events={"e": "$e"},
            created_users=["@a:hs"],
            created_rooms=["!r:hs"],
            active_ms=1200,
        )

        self.assertEqual(Checkpoint.from_json(checkpoint.to_json()), checkpoint)

    def test_no_checkpoint_is_a_fresh_start(self):
        self.assertEqual(Checkpoint.from_json(None), Checkpoint())

    def test_refuses_a_checkpoint_in_another_format(self):
        with self.assertRaises(IncompatibleCheckpointError):
            Checkpoint.from_json({"version": 0, "next_index": 3})

    def test_records_a_created_resource_once(self):
        checkpoint = Checkpoint()

        checkpoint.add_created_user("@a:hs")
        checkpoint.add_created_user("@a:hs")
        checkpoint.add_created_room("!r:hs")
        checkpoint.add_created_room("!r:hs")

        self.assertEqual(checkpoint.created_users, ["@a:hs"])
        self.assertEqual(checkpoint.created_rooms, ["!r:hs"])


if __name__ == "__main__":
    unittest.main()
