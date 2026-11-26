"""Tests for DMBatchUpdater."""
import unittest
from unittest.mock import AsyncMock, MagicMock

from connect.notification_preferences.batch_update import DMBatchUpdater

user_id = "@alice:powerhrg.com"
other_user = "@bob:powerhrg.com"
room_id = "!room1:powerhrg.com"
room_id_2 = "!room2:powerhrg.com"


def make_push_rule(user_name, room_id, rule_id_suffix, dm_creator=None):
    return {
        "room_id": room_id,
        "user_name": user_name,
        "dm_creator": dm_creator or user_name,
        "rule_id": f"global/override/{rule_id_suffix};{room_id}",
    }


def all_and_me_rules(user_name, room_id, dm_creator=None):
    return [
        make_push_rule(
            user_name, room_id, "connect.room..mentions_or_all.disable_all", dm_creator
        ),
        make_push_rule(
            user_name, room_id, "connect.room..mentions_or_all.mention_all", dm_creator
        ),
        make_push_rule(
            user_name, room_id, "connect.room..mentions_or_all.mention_me", dm_creator
        ),
        make_push_rule(user_name, room_id, "connect.room..suppress.edits", dm_creator),
    ]


def mock_hs(push_rules):
    hs = MagicMock()
    db_pool = MagicMock()
    db_pool.runInteraction = AsyncMock(return_value=push_rules)
    hs.get_datastores.return_value.main.db_pool = db_pool
    hs.get_datastores.return_value.main.get_account_data_for_room_and_type = AsyncMock(
        return_value=None
    )
    hs.get_datastores.return_value.main.delete_push_rule = AsyncMock()
    hs.get_push_rules_handler.return_value.notify_user = MagicMock()
    return hs


class DMBatchUpdaterAnalyzeTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test__analyze__no_push_rules(self):
        hs = mock_hs([])
        updater = DMBatchUpdater(hs)
        summary = await updater.analyze()

        self.assertEqual(summary["total_dm_push_rules"], 0)
        self.assertEqual(summary["total_dms"], 0)
        self.assertEqual(summary["affected_users"], 0)

    async def test__analyze__single_dm_all_and_me(self):
        rules = all_and_me_rules(user_id, room_id)
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        summary = await updater.analyze()

        self.assertEqual(summary["total_dm_push_rules"], 4)
        self.assertEqual(summary["total_dms"], 1)
        self.assertEqual(summary["affected_users"], 1)
        self.assertEqual(summary["preference_types"], {"AllAndMePreference": 1})

    async def test__analyze__creator_tracking(self):
        rules = all_and_me_rules(user_id, room_id, dm_creator=user_id)
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        summary = await updater.analyze()

        self.assertEqual(summary["user_is_dm_creator"], 1)
        self.assertEqual(summary["user_is_not_dm_creator"], 0)

    async def test__analyze__non_creator(self):
        rules = all_and_me_rules(user_id, room_id, dm_creator=other_user)
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        summary = await updater.analyze()

        self.assertEqual(summary["user_is_dm_creator"], 0)
        self.assertEqual(summary["user_is_not_dm_creator"], 1)

    async def test__analyze__account_data_present(self):
        rules = all_and_me_rules(user_id, room_id)
        hs = mock_hs(rules)
        hs.get_datastores.return_value.main.get_account_data_for_room_and_type = (
            AsyncMock(return_value={"level": "all_and_me"})
        )
        updater = DMBatchUpdater(hs)
        summary = await updater.analyze()

        self.assertEqual(summary["has_account_data"], 1)
        self.assertEqual(summary["missing_account_data"], 0)

    async def test__analyze__account_data_missing(self):
        rules = all_and_me_rules(user_id, room_id)
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        summary = await updater.analyze()

        self.assertEqual(summary["has_account_data"], 0)
        self.assertEqual(summary["missing_account_data"], 1)

    async def test__analyze__multiple_dms(self):
        rules = all_and_me_rules(user_id, room_id) + all_and_me_rules(
            other_user, room_id_2
        )
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        summary = await updater.analyze()

        self.assertEqual(summary["total_dms"], 2)
        self.assertEqual(summary["affected_users"], 2)

    async def test__analyze__populates_entries(self):
        rules = all_and_me_rules(user_id, room_id)
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        await updater.analyze()

        self.assertEqual(len(updater.entries), 1)
        entry = updater.entries[0]
        self.assertEqual(entry["room_id"], room_id)
        self.assertEqual(entry["user_name"], user_id)
        self.assertEqual(entry["dm_creator"], user_id)
        self.assertTrue(entry["is_creator"])
        self.assertFalse(entry["has_account_data"])
        self.assertEqual(entry["preference_type"], "AllAndMePreference")


class DMBatchUpdaterDeleteTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test__delete__no_push_rules(self):
        hs = mock_hs([])
        updater = DMBatchUpdater(hs)
        await updater.delete()

        store = hs.get_datastores.return_value.main
        store.delete_push_rule.assert_not_called()
        hs.get_push_rules_handler.return_value.notify_user.assert_not_called()

    async def test__delete__deletes_all_rules(self):
        rules = all_and_me_rules(user_id, room_id)
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        await updater.delete()

        store = hs.get_datastores.return_value.main
        self.assertEqual(store.delete_push_rule.call_count, 4)
        for rule in rules:
            store.delete_push_rule.assert_any_call(rule["user_name"], rule["rule_id"])

    async def test__delete__notifies_affected_users(self):
        rules = all_and_me_rules(user_id, room_id) + all_and_me_rules(
            other_user, room_id_2
        )
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        await updater.delete()

        notify = hs.get_push_rules_handler.return_value.notify_user
        self.assertEqual(notify.call_count, 2)
        notify.assert_any_call(user_id)
        notify.assert_any_call(other_user)

    async def test__delete__notifies_user_once_for_multiple_rules(self):
        rules = all_and_me_rules(user_id, room_id)
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        await updater.delete()

        notify = hs.get_push_rules_handler.return_value.notify_user
        notify.assert_called_once_with(user_id)

    async def test__delete__checks_remaining_rules(self):
        rules = all_and_me_rules(user_id, room_id)
        hs = mock_hs(rules)
        updater = DMBatchUpdater(hs)
        await updater.delete()

        db_pool = hs.get_datastores.return_value.main.db_pool
        self.assertEqual(db_pool.runInteraction.call_count, 2)


if __name__ == "__main__":
    unittest.main()
