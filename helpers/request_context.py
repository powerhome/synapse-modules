"""Request context for tracing CPG requests across connect-server modules."""

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

CPG_REQUEST_ID_HEADER = b"X-CPG-Request-Id"

_cpg_request_id: ContextVar[str | None] = ContextVar("cpg_request_id", default=None)
_cpg_source_action: ContextVar[str | None] = ContextVar(
    "cpg_source_action", default=None
)

logger = logging.getLogger(__name__)


def init_request_context(
    request=None, *, request_id: str | None = None, action: str | None = None
) -> str:
    """Extract or generate a CPG request ID and set the context.

    The effective request ID is resolved in priority order: an explicit
    ``request_id`` (used to re-hydrate context inside background processes
    where ``ContextVar``s do not propagate), then the ``X-CPG-Request-Id``
    header on the incoming request (sent by CPG/audiences for end-to-end
    correlation), and finally a fresh UUID so every operation is traceable.

    Args:
        request: Incoming HTTP request, or ``None`` to skip header lookup.
        request_id: Explicit request ID to reuse, e.g. captured before scheduling a background process.
        action: Optional logical action name stored for downstream logging.

    Returns:
        The effective CPG request ID.
    """
    if not request_id and request is not None:
        request_id = request.getHeader("X-CPG-Request-Id")

    if not request_id:
        request_id = str(uuid.uuid4())

    _cpg_request_id.set(request_id)
    if action:
        _cpg_source_action.set(action)

    return request_id


def get_request_id() -> str | None:
    return _cpg_request_id.get()


def get_source_action() -> str | None:
    return _cpg_source_action.get()


def cpg_headers() -> dict[bytes, list[bytes]]:
    """Build outbound headers carrying the CPG request ID.

    Returns:
        A single-header mapping for ``X-CPG-Request-Id`` when context has an
        ID; otherwise an empty dict.
    """
    request_id = get_request_id()
    if request_id:
        return {CPG_REQUEST_ID_HEADER: [request_id.encode("ascii")]}
    return {}


def log_event(
    logger: logging.Logger, message: str, level: int = logging.INFO, **fields
) -> None:
    """Emit a structured JSON log entry with CPG context automatically included.

    Args:
        logger: Logger used to emit the JSON line.
        message: Primary human-readable message for the entry.
        level: ``logging`` level for the emitted record (default ``INFO``).
        **fields: Additional keys merged into the structured JSON payload.
    """
    entry = {
        "cpg_request_id": get_request_id(),
        "source_action": get_source_action(),
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }
    entry.update(fields)
    logger.log(level, json.dumps(entry))
