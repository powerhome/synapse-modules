"""Tests for telling a passing outage from a real failure."""

import unittest

from synapse.api.errors import HttpResponseException, SynapseError
from twisted.internet import error as twisted_errors

from fixtures import retry
from fixtures.retry import Backoff, is_transient


class IsTransientTestSuite(unittest.TestCase):
    def test_an_unreachable_dependency_is_transient(self):
        self.assertTrue(is_transient(twisted_errors.ConnectionRefusedError()))

    def test_a_step_timeout_is_transient(self):
        # The class retry.py itself classifies (tests import the package by a
        # different path than the module does).
        self.assertTrue(is_transient(retry.StepTimeoutError("slow")))

    def test_a_gateway_error_is_transient(self):
        for code in (502, 503, 504):
            self.assertTrue(is_transient(HttpResponseException(code, "down", b"")))

    def test_a_client_or_server_error_is_not(self):
        for code in (400, 404, 409, 500):
            self.assertFalse(is_transient(HttpResponseException(code, "no", b"")))

    def test_a_synapse_error_is_not(self):
        self.assertFalse(is_transient(SynapseError(400, "bad ref")))


class BackoffTestSuite(unittest.TestCase):
    def test_doubles_up_to_the_cap_within_the_budget(self):
        backoff = Backoff(budget_s=40, first_s=1, cap_s=8)

        delays = []
        while (delay := backoff.next_delay()) is not None:
            delays.append(delay)

        self.assertEqual(delays, [1, 2, 4, 8, 8, 8, 8, 1])
        self.assertEqual(sum(delays), 40)


if __name__ == "__main__":
    unittest.main()
