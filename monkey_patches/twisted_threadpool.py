"""Twisted ThreadPool monkey patch to propagate context variables across thread boundaries."""

from __future__ import annotations

import contextvars

try:
    from twisted.python.threadpool import ThreadPool
except ImportError:
    ThreadPool = None


class TwistedThreadPoolPatch:
    """Stub module class.

    Synapse's ``modules:`` config imports this module, which triggers the
    ThreadPool monkey patch below at import time. The class itself does
    nothing; its only job is giving homeserver.yaml something to reference.
    """

    def __init__(self, config: dict, api) -> None:
        pass

    @staticmethod
    def parse_config(config: dict):
        return config


if ThreadPool is not None:
    _original_call_in_thread_with_callback = ThreadPool.callInThreadWithCallback

    def _patched_call_in_thread_with_callback(self, on_result, func, *args, **kw):
        """Preserve contextvars across Twisted thread-pool boundaries.

        contextvars don't cross Twisted thread boundaries by default.
        OpenTracing 2.4 and the NR agent rely on contextvars-style propagation.

        Args:
            self: ThreadPool instance.
            on_result: Callback invoked when the function completes.
            func: Function to execute in thread.
            args: Positional arguments to func.
            kw: Keyword arguments to func.

        Returns:
            Result from original callInThreadWithCallback.
        """
        ctx = contextvars.copy_context()

        def _wrapped():
            return ctx.run(func, *args, **kw)

        return _original_call_in_thread_with_callback(self, on_result, _wrapped)

    ThreadPool.callInThreadWithCallback = _patched_call_in_thread_with_callback
