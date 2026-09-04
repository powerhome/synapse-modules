"""Presence module."""

import logging
from typing import Any

from synapse.module_api import ModuleApi

from .api import PresenceResource

logger = logging.getLogger(__name__)


class Module:
    """A module that exposes a cached, paginated, long-polling presence endpoint.

    Registered only on the worker that also serves `/sync` long-polls, since it
    reuses that worker's `Notifier`/`PresenceEventSource` machinery directly
    rather than round-tripping to another process.
    """

    def __init__(self, config: dict[str, Any], api: ModuleApi) -> None:
        if api.worker_name == "synapse-sync-worker":
            logger.info(f"Registering PresenceResource on {api.worker_name}")
            api.register_web_resource(
                path="/_connect/presence",
                resource=PresenceResource(api._hs),
            )
