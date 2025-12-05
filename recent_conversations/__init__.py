"""Recent conversations module."""

import logging
from typing import Any

from synapse.module_api import ModuleApi

from .api import RecentConversationsResource

logger = logging.getLogger(__name__)


class Module:
    """A module that handles recent conversations."""

    def __init__(self, config: dict[str, Any], api: ModuleApi) -> None:
        if api.worker_name == "synapse-pagination-worker":
            logger.info(f"Registering RecentConversationsResource on {api.worker_name}")
            api.register_web_resource(
                path="/_connect/recent_conversations",
                resource=RecentConversationsResource(api._hs),
            )
