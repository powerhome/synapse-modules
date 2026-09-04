"""Appointment block events module."""

from pydantic import HttpUrl, SecretStr
from synapse.module_api import ModuleApi

from connect.base_config import BaseConfig

from .api import AppointmentBlockResource


class Config(BaseConfig):
    """Configuration for appointment block module."""

    hs_token: SecretStr
    bridge_base_url: HttpUrl


class Module:
    """A module for appointment blocks."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        api.register_web_resource(
            path="/_nitro/appointment_block",
            resource=AppointmentBlockResource(api._hs, config),
        )
