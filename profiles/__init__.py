"""Profiles module."""

import logging
from typing import Optional

from pydantic import HttpUrl, SecretStr
from synapse.module_api import ModuleApi

from connect.base_config import BaseConfig

from .api import ProfileResource
from .bridge_handler import BridgeHandler
from .handler import ProfileHandler
from .store import ProfileStore


class Config(BaseConfig):
    """Configuration for profiles module."""

    hs_token: Optional[SecretStr] = None
    bridge_base_url: Optional[HttpUrl] = None


class Module:
    """A module that handles profiles."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        hs = api._hs
        main_store = hs.get_datastores().main

        store = ProfileStore(main_store.db_pool)

        if "hs_token" in config and "bridge_base_url" in config:
            logging.info("Using Connect v2 bridge for profiles")
            token = config["hs_token"].encode("ascii")
            handler = BridgeHandler(store, hs, token, config["bridge_base_url"])
        else:
            logging.info("Using local profiles")
            handler = ProfileHandler(store)

        api.register_web_resource(
            path="/_connect/profiles", resource=ProfileResource(hs, handler)
        )
        api.register_web_resource(
            path="/_connect/directory", resource=ProfileResource(hs, handler)
        )
