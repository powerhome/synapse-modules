"""Populator module."""

import logging
from typing import Any

from pydantic import Field, SecretStr
from synapse.module_api import ModuleApi

from ..base_config import BaseConfig
from .populator import Populator
from .provisioning import ProvisioningResource
from .webhook import WebhookResource


class Config(BaseConfig):
    """Configuration for populator module."""

    hs_token: SecretStr
    idp_id: str = Field(min_length=1)


logger = logging.getLogger(__name__)


class Module:
    """A module that handles the user populator."""

    def __init__(self, config: dict[str, Any], api: ModuleApi) -> None:
        Config.model_validate(config)
        is_worker = api.worker_name is not None
        if is_worker:
            logger.info(
                f"Populator: not initializing module on worker: {api.worker_name}"
            )
            return

        logger.info("Populator: initializing module on main process")

        hs = api._hs
        logger.info("Initializing Populator")
        populator = Populator(config, hs)
        populator.request_synapse_audience()
        api.register_web_resource(
            path="/_populator/webhook", resource=WebhookResource(hs, populator)
        )
        api.register_web_resource(
            path="/_populator/users", resource=ProvisioningResource(hs, populator)
        )
        api.register_account_validity_callbacks(
            is_user_expired=populator.is_user_expired
        )
