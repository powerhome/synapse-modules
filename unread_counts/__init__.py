"""Unread counts module."""

import logging
from typing import Any

from synapse.module_api import ModuleApi

from .api import UnreadCountsResource

logger = logging.getLogger(__name__)


class Module:
    """A module that exposes the requester's per-room unread notification counts.

    Registered only on the generic worker, because that is where the deployment
    already routes `/_connect/*` — see the `location ~ ^/_connect(?!/presence)`
    block in `deploy/templates/nginx.synapse-generic-worker.conf`, which is mounted
    ahead of the main template's `/_connect` catch-all. Registering on the main
    process instead would make the endpoint unreachable.

    The endpoint only reads `event_push_summary`, so it needs no writer role and is
    deliberately kept off the sync worker.
    """

    def __init__(self, config: dict[str, Any], api: ModuleApi) -> None:
        if api.worker_name == "synapse-generic-worker":
            logger.info(f"Registering UnreadCountsResource on {api.worker_name}")
            api.register_web_resource(
                path="/_connect/unread_counts",
                resource=UnreadCountsResource(api._hs),
            )
