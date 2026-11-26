"""Notifying the Connect v2 bridge of changes made in Connect v3.

The bridge applies each change in Connect v2 as nitro-web's Nitro bot, so the
notification names the v3 user who made the change only for the logs.
"""

from typing import Optional

from pydantic import HttpUrl, SecretStr
from synapse.module_api import ModuleApi

from ..base_config import BaseConfig
from .client import BridgeClient, BridgeRefusedError, BridgeUnavailableError
from .notifier import BridgeNotifier, ExponentialBackoff

__all__ = [
    "BridgeClient",
    "BridgeConfig",
    "BridgeNotifier",
    "BridgeRefusedError",
    "BridgeUnavailableError",
    "ExponentialBackoff",
    "notifier_from_config",
]


class BridgeConfig(BaseConfig):
    """Where the Connect v2 bridge listens and the token it expects."""

    hs_token: SecretStr
    base_url: HttpUrl


def notifier_from_config(
    api: ModuleApi, bridge_config: Optional[dict]
) -> Optional[BridgeNotifier]:
    """Build a notifier from a module's `bridge` config block.

    Args:
        api: The module API, whose HTTP client reaches the bridge.
        bridge_config: The module's `bridge` config block, if any.

    Returns:
        None when the block is absent, i.e. Connect v2 is not bridged.
    """
    if not bridge_config:
        return None

    client = BridgeClient(
        api.http_client, bridge_config["hs_token"], bridge_config["base_url"]
    )
    return BridgeNotifier(api, client)
