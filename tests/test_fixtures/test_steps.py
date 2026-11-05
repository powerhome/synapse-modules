"""Tests for running provisioning steps under a timeout."""

import unittest

from twisted.internet import defer
from twisted.internet.task import Clock

from fixtures.steps import Steps, StepTimeoutError, TimedSteps


class StepsTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_step_result(self):
        async def step():
            return "done"

        result = await Steps().run(step)

        self.assertEqual(result, "done")


class TimedStepsTestSuite(unittest.TestCase):
    def test_returns_the_result_of_a_step_that_finishes_in_time(self):
        clock = Clock()

        async def step():
            return "done"

        outcome = defer.ensureDeferred(TimedSteps(clock, 60).run(step))

        self.assertEqual(self.successResultOf(outcome), "done")

    def test_fails_a_step_that_outlives_the_timeout(self):
        clock = Clock()

        async def step():
            await defer.Deferred()

        outcome = defer.ensureDeferred(TimedSteps(clock, 60).run(step))
        clock.advance(61)

        self.failureResultOf(outcome, StepTimeoutError)

    def test_cancels_the_step_that_timed_out(self):
        clock = Clock()
        pending = defer.Deferred()

        async def step():
            await pending

        outcome = defer.ensureDeferred(TimedSteps(clock, 60).run(step))
        clock.advance(61)

        self.failureResultOf(outcome, StepTimeoutError)
        self.assertTrue(pending.called)

    def successResultOf(self, d):  # noqa: N802
        results = []
        d.addBoth(results.append)
        self.assertEqual(len(results), 1)
        return results[0]

    def failureResultOf(self, d, error_type):  # noqa: N802
        failures = []
        d.addErrback(failures.append)
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0].check(error_type))
