"""Test-fixture provisioning module.

A single, environment-gated surface for minting test fixtures (users today;
rooms, events, and media in the future) against a real Synapse, so the CCL
e2e/load harness and server-side tests share one provisioning path instead of
the two uncoordinated mechanisms (the harness's shared-secret register and the
audiences populator) they use today.

It MUST be reachable in local, PR, and staging environments ONLY — never
production. The module registers no routes unless ``enabled`` is true, so even a
production misconfiguration exposes nothing.
"""

import logging
from typing import Any

from pydantic import SecretStr
from synapse.config._base import ConfigError
from synapse.module_api import ModuleApi

from connect.base_config import BaseConfig
from connect.fixtures.api import FixturesResource, ReceiptWriterResource
from connect.fixtures.provisioner import Provisioner
from connect.fixtures.receipts import ReceiptProvisioner

logger = logging.getLogger(__name__)


class Config(BaseConfig):
    """Configuration for the fixtures module."""

    enabled: bool = False
    shared_secret: SecretStr = SecretStr("")
    # ``oidc-<idp>`` auth-provider id (mirrors the populator). Present only where
    # OIDC is enabled; required for ``auth: oidc`` and directory enrichment.
    idp_id: str = ""
    # In-cluster audiences base URL; required for directory enrichment.
    audiences_base_url: str = ""


class Module:
    """Registers the fixtures routes when explicitly enabled."""

    def __init__(self, config: dict[str, Any], api: ModuleApi) -> None:
        parsed = Config.model_validate(config)

        if not parsed.enabled:
            logger.info("Fixtures: disabled, not registering routes")
            return

        if not parsed.shared_secret.get_secret_value():
            raise ConfigError("fixtures module is enabled but shared_secret is empty")

        hs = api._hs
        secret = parsed.shared_secret.get_secret_value()

        # Workers register nothing — except the receipts stream writer, which
        # hosts the internal endpoint the main-process scenario servlet forwards
        # receipts to (Synapse's receipts store only accepts writes there).
        if api.worker_name is not None:
            if hs.get_instance_name() in hs.config.worker.writers.receipts:
                api.register_web_resource(
                    path="/_fixtures/receipt",
                    resource=ReceiptWriterResource(hs, ReceiptProvisioner(hs, secret)),
                )
                logger.info("Fixtures receipt route registered on the receipts writer")
            return

        provisioner = Provisioner(
            hs,
            secret,
            idp_id=f"oidc-{parsed.idp_id}" if parsed.idp_id else None,
            audiences_base_url=parsed.audiences_base_url or None,
        )
        api.register_web_resource(
            path="/_fixtures",
            resource=FixturesResource(hs, provisioner),
        )
        logger.info("Fixtures module initialized, routes registered")
