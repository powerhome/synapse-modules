"""Corporate events module."""

from pydantic import HttpUrl, SecretStr
from synapse.module_api import ModuleApi

from connect.base_config import BaseConfig

from .api import CorporateEventResource


class Config(BaseConfig):
    """Configuration for corporate events module."""

    hs_token: SecretStr
    bridge_base_url: HttpUrl


class Module:
    """A module for corporate events."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        api.register_web_resource(
            path="/_nitro/corporate_events",
            resource=CorporateEventResource(api._hs, config),
        )
