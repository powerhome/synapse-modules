"""Test-fixture provisioning module.

A single, environment-gated surface for minting test fixtures (users, rooms,
events, reactions, receipts, media and more) against a real Synapse, so the CCL
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
from connect.fixtures.runner import DeploymentRunner, RunnerLimits
from connect.fixtures.steps import TimedSteps

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
    # Bot accounts an archived room keeps as members (mirrors the archive
    # module's ``bot_user_ids``).
    bot_user_ids: list[str] = []
    # The Connect Access Group's external id on a gated tenant (Power); empty
    # elsewhere. Directory users are put in it, as CPG gates Connect access on it.
    connect_access_group_external_id: str = ""
    # Shared-secret token for uploading to the media repo; required for media.
    media_repo_token: SecretStr = SecretStr("")
    # Longest any one provisioning call may run before it is retried or fails.
    step_timeout_s: float = 60.0
    # How long one step may keep retrying a dependency that is down (e.g. CPG
    # or the media repo mid-redeploy) before its deployment fails.
    retry_budget_s: float = 600.0
    # Largest scenario a deployment accepts, and its longest running time.
    max_resources: int = 100_000
    max_duration_s: float = 2 * 60 * 60
    # How many deployments run at once; the rest queue.
    max_concurrent: int = 4
    # The longest `POST /_fixtures/deployments?wait=` holds before answering 202.
    wait_max_s: float = 60.0


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
            bot_user_ids=parsed.bot_user_ids,
            media_repo_token=parsed.media_repo_token.get_secret_value(),
            connect_access_group_external_id=parsed.connect_access_group_external_id,
            steps=TimedSteps(hs.get_reactor(), parsed.step_timeout_s),
            retry_budget_s=parsed.retry_budget_s,
        )
        runner = DeploymentRunner(
            hs,
            api,
            provisioner,
            limits=RunnerLimits(
                max_resources=parsed.max_resources,
                max_duration_s=parsed.max_duration_s,
                max_concurrent=parsed.max_concurrent,
                wait_max_s=parsed.wait_max_s,
            ),
        )
        api.register_web_resource(
            path="/_fixtures",
            resource=FixturesResource(hs, provisioner, runner),
        )
        # Pick up deployments a previous process left unfinished — after a
        # redeploy, say — once start-up (and the migrations) are done.
        api.delayed_background_call(
            1000, runner.resume, desc="fixtures_resume_deployments"
        )
        logger.info("Fixtures module initialized, routes registered")
