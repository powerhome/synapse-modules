"""Tests for the up-front scenario checks."""

import unittest
from http import HTTPStatus

from synapse.api.errors import SynapseError

from fixtures.scenario_check import check_refs, check_size, resource_count


class ResourceCountTestSuite(unittest.TestCase):
    def test_counts_every_listed_resource_and_membership(self):
        scenario = {
            "users": [{"ref": "a"}, {"ref": "b"}],
            "rooms": [{"ref": "r", "members": ["a", "b"], "left": ["b"]}],
            "events": [{}, {}, {}],
        }

        self.assertEqual(resource_count(scenario), 9)


class CheckSizeTestSuite(unittest.TestCase):
    def test_accepts_a_scenario_at_the_limit(self):
        check_size({"users": [{}, {}]}, 2)

    def test_rejects_a_scenario_over_the_limit_with_the_hint(self):
        with self.assertRaises(SynapseError) as ctx:
            check_size({"users": [{}, {}, {}]}, 2, hint="; go elsewhere")

        self.assertEqual(ctx.exception.code, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.assertIn("go elsewhere", ctx.exception.msg)


class CheckRefsTestSuite(unittest.TestCase):
    def test_accepts_a_scenario_whose_refs_all_resolve(self):
        check_refs(
            {
                "users": [{"ref": "alice"}, {"ref": "bob"}],
                "rooms": [{"ref": "r", "creator": "alice", "members": ["bob"]}],
                "events": [
                    {"ref": "e1", "room": "r", "sender": "alice"},
                    {"room": "r", "sender": "bob", "reply_to": "e1"},
                ],
                "media": [{"ref": "m1", "room": "r", "sender": "bob"}],
                "reactions": [{"room": "r", "sender": "bob", "event": "m1"}],
                "redactions": [{"room": "r", "sender": "alice", "event": "e1"}],
                "receipts": [{"room": "r", "user": "bob", "event": "e1"}],
            }
        )

    def test_rejects_an_undeclared_room_member(self):
        with self.assertRaises(SynapseError) as ctx:
            check_refs(
                {
                    "users": [{"ref": "alice"}],
                    "rooms": [{"ref": "r", "creator": "alice", "members": ["eve"]}],
                }
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        self.assertIn("eve", ctx.exception.msg)

    def test_rejects_an_event_in_an_undeclared_room(self):
        with self.assertRaises(SynapseError):
            check_refs(
                {
                    "users": [{"ref": "alice"}],
                    "events": [{"room": "nowhere", "sender": "alice"}],
                }
            )

    def test_rejects_a_reply_to_a_later_event(self):
        with self.assertRaises(SynapseError):
            check_refs(
                {
                    "users": [{"ref": "alice"}],
                    "rooms": [{"ref": "r", "creator": "alice"}],
                    "events": [
                        {"room": "r", "sender": "alice", "reply_to": "e2"},
                        {"ref": "e2", "room": "r", "sender": "alice"},
                    ],
                }
            )

    def test_rejects_a_reaction_to_an_undeclared_event(self):
        with self.assertRaises(SynapseError):
            check_refs(
                {
                    "users": [{"ref": "alice"}],
                    "rooms": [{"ref": "r", "creator": "alice"}],
                    "reactions": [{"room": "r", "sender": "alice", "event": "e9"}],
                }
            )

    def test_rejects_a_receipt_for_an_undeclared_user(self):
        with self.assertRaises(SynapseError):
            check_refs(
                {
                    "users": [{"ref": "alice"}],
                    "rooms": [{"ref": "r", "creator": "alice"}],
                    "events": [{"ref": "e1", "room": "r", "sender": "alice"}],
                    "receipts": [{"room": "r", "user": "eve", "event": "e1"}],
                }
            )


class SameRoomTestSuite(unittest.TestCase):
    def scenario(self, **extra):
        return {
            "users": [{"ref": "alice"}],
            "rooms": [
                {"ref": "r1", "creator": "alice"},
                {"ref": "r2", "creator": "alice"},
            ],
            "events": [{"ref": "e1", "room": "r1", "sender": "alice"}],
            **extra,
        }

    def test_rejects_a_redaction_of_an_event_in_another_room(self):
        with self.assertRaises(SynapseError) as ctx:
            check_refs(
                self.scenario(
                    redactions=[{"room": "r2", "sender": "alice", "event": "e1"}]
                )
            )

        self.assertIn("is in room 'r1', not 'r2'", ctx.exception.msg)

    def test_rejects_a_reaction_or_receipt_in_another_room(self):
        for extra in (
            {"reactions": [{"room": "r2", "sender": "alice", "event": "e1"}]},
            {"receipts": [{"room": "r2", "user": "alice", "event": "e1"}]},
        ):
            with self.assertRaises(SynapseError):
                check_refs(self.scenario(**extra))

    def test_rejects_a_reply_to_an_event_in_another_room(self):
        scenario = self.scenario()
        scenario["events"].append({"room": "r2", "sender": "alice", "reply_to": "e1"})

        with self.assertRaises(SynapseError):
            check_refs(scenario)
