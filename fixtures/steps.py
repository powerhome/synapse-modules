"""Runs one provisioning step, optionally bounded by a timeout."""

from typing import Any, Awaitable, Callable

from synapse.logging.context import make_deferred_yieldable, run_in_background
from synapse.util.async_helpers import timeout_deferred
from twisted.internet import defer
from twisted.internet.interfaces import IReactorTime


class StepTimeoutError(Exception):
    """A provisioning step ran longer than its timeout."""


class Steps:
    """Runs a provisioning step as-is (no timeout)."""

    async def run(self, step: Callable[[], Awaitable[Any]]) -> Any:
        """Run one provisioning step and return its result.

        Args:
            step (Callable[[], Awaitable[Any]]): The provisioning call, its arguments bound.

        Returns:
            Any: Whatever ``step`` returns.
        """
        return await step()


class TimedSteps(Steps):
    """Runs each provisioning step under a timeout.

    One hung call — a slow CPG or media-repo request, say — then fails its
    scenario instead of stalling it.
    """

    def __init__(self, reactor: IReactorTime, seconds: float):
        self._reactor = reactor
        self._seconds = seconds

    async def run(self, step: Callable[[], Awaitable[Any]]) -> Any:
        """Run one provisioning step, cancelling it after the configured timeout.

        Args:
            step (Callable[[], Awaitable[Any]]): The provisioning call, its arguments bound.

        Returns:
            Any: Whatever ``step`` returns.

        Raises:
            StepTimeoutError: If the step outlives the timeout.
        """
        bounded = timeout_deferred(
            run_in_background(step), self._seconds, self._reactor
        )
        try:
            return await make_deferred_yieldable(bounded)
        except defer.TimeoutError:
            raise StepTimeoutError(
                f"a fixtures step ran longer than {self._seconds:g}s"
            ) from None
