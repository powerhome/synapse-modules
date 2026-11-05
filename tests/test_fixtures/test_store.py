"""Tests for the deployments table's transactions."""

import json
import unittest
from unittest.mock import MagicMock

from fixtures.store import DeploymentRecord, DeploymentStore


def row(**changes):
    values = {
        "id": "dep1",
        "key": None,
        "run_id": "run-a",
        "status": "queued",
        "cancel_requested": 0,
        "credentials_claimed": 1,
        "scenario": json.dumps({"run_id": "run-a"}),
        "checkpoint": None,
        "fixed_localparts": json.dumps(["bob.smith"]),
        "error": None,
        "error_code": None,
        "errcode": None,
        "created_ms": 1,
        "heartbeat_ms": None,
        "finished_ms": None,
    }
    return {**values, **changes}


class DeploymentRecordTestSuite(unittest.TestCase):
    def test_decodes_json_columns_and_flags(self):
        record = DeploymentRecord.from_row(row(checkpoint='{"next_index": 2}'))

        self.assertEqual(record.scenario, {"run_id": "run-a"})
        self.assertEqual(record.checkpoint, {"next_index": 2})
        self.assertEqual(record.fixed_localparts, ["bob.smith"])
        self.assertIs(record.credentials_claimed, True)
        self.assertIs(record.cancel_requested, False)

    def test_is_unfinished_while_it_still_owns_work(self):
        for status in ("queued", "running", "deprovisioning"):
            self.assertTrue(DeploymentRecord.from_row(row(status=status)).is_unfinished)
        for status in ("complete", "failed", "cancelled", "deprovisioned"):
            self.assertFalse(
                DeploymentRecord.from_row(row(status=status)).is_unfinished
            )


class TransactionsTestSuite(unittest.TestCase):
    def test_credentials_are_claimed_by_exactly_one_caller(self):
        txn = MagicMock()
        txn.rowcount = 1

        claimed = DeploymentStore._claim_credentials_txn(txn, "dep1")

        sql = txn.execute.call_args.args[0]
        self.assertIn("credentials_claimed = FALSE", sql)
        self.assertTrue(claimed)

    def test_a_second_claim_finds_nothing(self):
        txn = MagicMock()
        txn.rowcount = 0

        self.assertFalse(DeploymentStore._claim_credentials_txn(txn, "dep1"))

    def test_unfinished_can_be_narrowed_to_a_run(self):
        txn = MagicMock()
        txn.__iter__.return_value = iter([])

        DeploymentStore._select_unfinished_txn(txn, "run-a")

        sql, args = txn.execute.call_args.args
        self.assertIn("run_id = ?", sql)
        self.assertEqual(args[-1], "run-a")


if __name__ == "__main__":
    unittest.main()
