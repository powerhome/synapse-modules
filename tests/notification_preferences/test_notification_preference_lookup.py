"""Tests for NotificationPreferenceLookup."""

import unittest

from connect.notification_preferences.notification_preferences import (
    AllAndMePreference,
    EveryMessagePreference,
    JustMePreference,
    MissingPreference,
    NoNotificationsPreference,
    NotificationPreferenceLookup,
)

user_id = "@alice:powerhrg.com"
room_id = "!room1:powerhrg.com"


def make_rule(rule_id):
    return {"rule_id": rule_id, "user_name": user_id}


class NotificationPreferenceLookupTestSuite(unittest.TestCase):
    def test__find__empty_rules(self):
        lookup = NotificationPreferenceLookup(user_id, room_id, [])
        result = lookup.find()
        self.assertIsInstance(result, MissingPreference)

    def test__find__all_and_me(self):
        rules = [
            make_rule(
                f"global/underride/connect.room..mentions_or_all.disable_all;{room_id}"
            ),
            make_rule(
                f"global/override/connect.room..mentions_or_all.mention_all;{room_id}"
            ),
            make_rule(
                f"global/override/connect.room..mentions_or_all.mention_me;{room_id}"
            ),
            make_rule(f"global/override/connect.room..suppress.edits;{room_id}"),
        ]
        lookup = NotificationPreferenceLookup(user_id, room_id, rules)
        result = lookup.find()
        self.assertIsInstance(result, AllAndMePreference)

    def test__find__just_me(self):
        rules = [
            make_rule(
                f"global/underride/connect.room..only_mentions.disable_all;{room_id}"
            ),
            make_rule(
                f"global/override/connect.room..only_mentions.disable_mention_all;{room_id}"
            ),
            make_rule(
                f"global/override/connect.room..only_mentions.mention_me;{room_id}"
            ),
            make_rule(f"global/override/connect.room..suppress.edits;{room_id}"),
        ]
        lookup = NotificationPreferenceLookup(user_id, room_id, rules)
        result = lookup.find()
        self.assertIsInstance(result, JustMePreference)

    def test__find__no_notifications(self):
        rules = [
            make_rule(f"global/override/connect.room.disable_all;{room_id}"),
        ]
        lookup = NotificationPreferenceLookup(user_id, room_id, rules)
        result = lookup.find()
        self.assertIsInstance(result, NoNotificationsPreference)

    def test__find__every_message(self):
        rules = [
            make_rule(f"global/override/connect.room..suppress.edits;{room_id}"),
        ]
        lookup = NotificationPreferenceLookup(user_id, room_id, rules)
        result = lookup.find()
        self.assertIsInstance(result, EveryMessagePreference)

    def test__find__unrecognized_rules(self):
        rules = [
            make_rule("global/override/some.unknown.rule;!room1:powerhrg.com"),
        ]
        lookup = NotificationPreferenceLookup(user_id, room_id, rules)
        result = lookup.find()
        self.assertIsInstance(result, MissingPreference)


if __name__ == "__main__":
    unittest.main()
