"""Debugpy listener for development."""

import logging
import os

try:
    import debugpy
except ImportError:
    debugpy = None  # type: ignore[assignment]

_listening = False


def listen_if_enabled() -> None:
    global _listening
    port = os.environ.get("DEBUGPY_PORT")
    if not port or _listening:
        return
    if debugpy is None:
        raise RuntimeError("DEBUGPY_PORT is set but debugpy is not installed")
    _listening = True
    debugpy.listen(("0.0.0.0", int(port)))  # noqa: S104

    logging.getLogger(__name__).info("debugpy listening on port %s", port)
    if os.environ.get("DEBUGPY_WAIT") == "true":
        logging.getLogger(__name__).info("waiting for debugger to attach")
        debugpy.wait_for_client()
