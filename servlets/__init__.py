"""Module to register custom servlets (to override default Synapse API endpoints)."""

import logging
from typing import TYPE_CHECKING, Optional

from pydantic import Field, HttpUrl, SecretStr
from synapse.http.server import HttpServer
from synapse.module_api import ModuleApi
from synapse.rest import SERVLET_GROUPS

from connect.audiences.connect_room_membership_servlet import (
    ConnectRoomMembershipRestServlet,
)
from connect.audiences.connect_room_state_event_servlet import (
    ConnectRoomStateEventRestServlet,
)
from connect.crypto import (
    ConnectRoomInitialSyncRestServlet,
    ConnectRoomMessageListRestServlet,
    ConnectRoomSendEventRestServlet,
    ConnectSyncRestServlet,
)
from connect.custom_types import CUSTOM_EVENT_TYPE
from connect.notification_preferences.connect_push_rule_rest_servlet import (
    ConnectPushRuleRestServlet,
)
from connect.search.connect_search_register_servlets import ConnectSearchRestServlet

from ..audiences.connect_join_room_alias_servlet import ConnectJoinRoomAliasServlet
from ..base_config import BaseConfig
from ..receipts import ConnectReadMarkerRestServlet

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class BridgeServlet(BaseConfig):
    """Bridge servlet configuration."""

    hs_token: SecretStr
    bridge_base_url: HttpUrl


class AudiencesServlet(BaseConfig):
    """Audiences servlet configuration."""

    hs_token: SecretStr
    idp_id: str = Field(min_length=1)
    services_enabled: bool = False
    bot_user_ids: list[str] = Field(min_items=1, default_factory=list)


class CryptoServlet(BaseConfig):
    """Crypto servlet configuration."""

    enabled: bool
    key: SecretStr = Field(min_length=1)


class Config(BaseConfig):
    """Configuration for servlets module."""

    bridge: Optional[BridgeServlet] = None
    audiences: AudiencesServlet
    crypto: CryptoServlet
    notification_preference_event_type: CUSTOM_EVENT_TYPE


class Module:
    """Registers custom servlets"""

    def __init__(self, config: dict, _api: ModuleApi):
        Config.model_validate(config)
        self.config = config
        self.crypto_enabled = self.config.get("crypto", {}).get("enabled", False)

        # see https://github.com/element-hq/synapse/blob/v1.138.2/synapse/rest/__init__.py#L80
        register_servlets_functions = list(SERVLET_GROUPS["client"])
        register_servlets_functions.append(self.room_register_servlets)
        register_servlets_functions.append(self.room_register_deprecated_servlets)
        register_servlets_functions.append(self.sync_register_servlets)
        register_servlets_functions.append(self.push_rules_register_servlets)
        register_servlets_functions.append(self.read_marker_register_servlets)
        SERVLET_GROUPS["client"] = tuple(register_servlets_functions)

    def room_register_servlets(self, hs: "HomeServer", http_server: HttpServer) -> None:
        if self.crypto_enabled:
            ConnectRoomMessageListRestServlet(hs).register(http_server)
            ConnectRoomSendEventRestServlet(hs).register(http_server)
            key = self.config.get("crypto", {}).get("key")
            ConnectSearchRestServlet(hs, key).register(http_server)

        audiences_config = self.config.get("audiences", {})
        if audiences_config.get("services_enabled"):
            logger.info(
                "Registering audiences custom room membership and state event servlets"
            )
            ConnectJoinRoomAliasServlet(hs, audiences_config).register(http_server)
            ConnectRoomMembershipRestServlet(hs, audiences_config).register(http_server)
            ConnectRoomStateEventRestServlet(hs, audiences_config).register(http_server)
        else:
            logger.info(
                "Not registering audiences custom room membership or state event servlets"
            )

    def push_rules_register_servlets(
        self, hs: "HomeServer", http_server: HttpServer
    ) -> None:
        api = hs.get_module_api()
        is_worker = api.worker_name is not None
        if is_worker:
            logger.info(
                f"Not initializing ConnectPushRuleRestServlet module on worker: {api.worker_name}"
            )
            return

        logger.info("Initializing ConnectPushRuleRestServlet module main process")
        ConnectPushRuleRestServlet(hs, self.config).register(http_server)

    def read_marker_register_servlets(
        self, hs: "HomeServer", http_server: HttpServer
    ) -> None:
        if hs.get_instance_name() not in hs.config.worker.writers.receipts:
            return

        logger.info("Registering ConnectReadMarkerRestServlet")
        ConnectReadMarkerRestServlet(hs).register(http_server)

    def room_register_deprecated_servlets(
        self, hs: "HomeServer", http_server: HttpServer
    ) -> None:
        if self.crypto_enabled:
            ConnectRoomInitialSyncRestServlet(hs).register(http_server)

    def sync_register_servlets(self, hs: "HomeServer", http_server: HttpServer) -> None:
        if self.crypto_enabled:
            ConnectSyncRestServlet(hs).register(http_server)
