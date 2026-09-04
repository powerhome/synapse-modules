"""Readiness module for Synapse.

Registers an internal `/ready` endpoint for Kubernetes readiness probing.
"""

from typing import Any

from synapse.logging import logging
from synapse.module_api import ModuleApi

from .api import ReadinessResource

logger = logging.getLogger(__name__)


class Module:
    """Synapse module that exposes a readiness endpoint."""

    def __init__(self, config: dict[str, Any], api: ModuleApi):
        # No required config at this time; keep argument for Synapse module API compatibility.
        api.register_web_resource(
            path="/ready",
            resource=ReadinessResource(api._hs),
        )
        logger.info("Registered readiness endpoint at /ready")
