"""Tells a passing outage from a real failure, and retries through the former."""

from synapse.api.errors import HttpResponseException
from synapse.http import RequestTimedOutError
from twisted.internet import error as twisted_errors
from twisted.web import _newclient

from connect.fixtures.steps import StepTimeoutError

# A dependency answering with one of these is restarting or overloaded.
TRANSIENT_STATUSES = (502, 503, 504)

# Raised when a dependency cannot be reached at all, e.g. while it redeploys.
UNREACHABLE = (
    twisted_errors.ConnectError,
    twisted_errors.ConnectionLost,
    twisted_errors.ConnectionClosed,
    twisted_errors.DNSLookupError,
    twisted_errors.TimeoutError,
    _newclient.ResponseNeverReceived,
    _newclient.RequestTransmissionFailed,
    _newclient.ResponseFailed,
    RequestTimedOutError,
    StepTimeoutError,
)


def is_transient(error: BaseException) -> bool:
    """Whether ``error`` means "try again shortly" rather than "this is wrong".

    A redeploy of connect-server restarts CPG, the media repo and the Synapse
    workers alongside the main process, so a step can find a dependency briefly
    gone. Those failures are retried; anything else — a bad ref, a Fixed-name
    conflict, a 4xx — fails the deployment.

    Args:
        error (BaseException): What a provisioning step raised.

    Returns:
        bool: True if retrying could succeed.
    """
    if isinstance(error, UNREACHABLE):
        return True
    if isinstance(error, HttpResponseException):
        return error.code in TRANSIENT_STATUSES
    return False


class Backoff:
    """Delays between retries of one step: doubling, capped, within a budget."""

    def __init__(self, budget_s: float, first_s: float = 1.0, cap_s: float = 30.0):
        self._budget_s = budget_s
        self._next_s = first_s
        self._cap_s = cap_s
        self._spent_s = 0.0

    def next_delay(self) -> float | None:
        """The delay before the next retry, or ``None`` once the budget is spent.

        Returns:
            float | None: Seconds to wait, or ``None`` to give up.
        """
        if self._spent_s >= self._budget_s:
            return None
        delay = min(self._next_s, self._cap_s, self._budget_s - self._spent_s)
        self._spent_s += delay
        self._next_s = min(self._next_s * 2, self._cap_s)
        return delay
