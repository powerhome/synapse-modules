"""Tests for the admission gate."""

import unittest
from http import HTTPStatus

from synapse.api.errors import SynapseError

from fixtures.gate import AdmissionGate


class AdmissionGateTestSuite(unittest.TestCase):
    def test_a_teardown_waits_for_an_admission_of_its_run(self):
        gate = AdmissionGate()
        gate.begin_admission("run-a", None, [])

        with self.assertRaises(SynapseError) as ctx:
            gate.begin_teardown("run-a")

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)

    def test_an_admission_waits_for_a_teardown_of_its_run(self):
        gate = AdmissionGate()
        gate.begin_teardown("run-a")

        with self.assertRaises(SynapseError):
            gate.begin_admission("run-a", None, [])

    def test_a_global_teardown_blocks_every_run(self):
        gate = AdmissionGate()
        gate.begin_teardown(None)

        with self.assertRaises(SynapseError):
            gate.begin_admission("run-b", None, [])

    def test_a_global_teardown_waits_for_any_admission(self):
        gate = AdmissionGate()
        gate.begin_admission("run-b", None, [])

        with self.assertRaises(SynapseError):
            gate.begin_teardown(None)

    def test_other_runs_proceed_side_by_side(self):
        gate = AdmissionGate()
        gate.begin_admission("run-a", None, [])

        gate.begin_teardown("run-b")

    def test_two_admissions_cannot_claim_one_fixed_name(self):
        gate = AdmissionGate()
        gate.begin_admission("run-a", None, ["bob.smith"])

        with self.assertRaises(SynapseError):
            gate.begin_admission("run-b", None, ["bob.smith"])

    def test_two_admissions_cannot_claim_one_key(self):
        gate = AdmissionGate()
        gate.begin_admission("run-a", "k1", [])

        with self.assertRaises(SynapseError):
            gate.begin_admission("run-a", "k1", [])

    def test_ending_releases_what_was_claimed(self):
        gate = AdmissionGate()
        gate.begin_admission("run-a", "k1", ["bob.smith"])
        gate.begin_admission("run-a", None, [])

        gate.end_admission("run-a", "k1", ["bob.smith"])
        gate.begin_admission("run-b", "k1", ["bob.smith"])
        gate.end_admission("run-a", None, [])
        gate.begin_teardown("run-a")
        gate.end_teardown("run-a")

        gate.begin_admission("run-a", None, [])


if __name__ == "__main__":
    unittest.main()
