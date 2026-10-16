"""Auth introspect module.

Exposes an internal endpoint that the presence service's sidecar nginx calls
via `auth_request` to validate a client's access token and learn its user.
"""

from typing import Any

from synapse.logging import logging
from synapse.module_api import ModuleApi

from .api import INTROSPECT_PATH, IntrospectResource

logger = logging.getLogger(__name__)

# The only worker the presence sidecar is pointed at. Registering nowhere else
# keeps the endpoint off the main process, which the public `/_connect`
# fallback in `nginx.conf.template` proxies to.
INTROSPECT_WORKER = "synapse-generic-worker"


class Module:
    """Synapse module that exposes the auth introspect endpoint."""

    def __init__(self, config: dict[str, Any], api: ModuleApi):
        if api.worker_name == INTROSPECT_WORKER:
            api.register_web_resource(
                path=INTROSPECT_PATH,
                resource=IntrospectResource(api._hs),
            )
            logger.info(f"Registered {INTROSPECT_PATH} on {api.worker_name}")
