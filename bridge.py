"""Glue between bridges and Synapse."""

import logging
from typing import Collection, Optional, Tuple, Union

import attr
from pydantic import Field, HttpUrl, SecretStr
from synapse.module_api import NOT_SPAM, ModuleApi, errors

from .base_config import BaseConfig


class Config(BaseConfig):
    """Configuration for bridge authenticator module."""

    enabled: bool
    idp_id: str = Field(min_length=1)
    bot_user_id: str = Field(min_length=1)
    hs_token: SecretStr
    bridge_base_url: HttpUrl
    remote_user_id_field: str = Field(min_length=1)


logger = logging.getLogger(__name__)


@attr.s
class OidcSession:
    """Session data."""

    expires_at = attr.ib(type=int)
    access_token = attr.ib(type=str)
    refresh_token = attr.ib(type=str)
    remote_user_id = attr.ib(type=int)


# Map from user localpart to OidcSession.
oidc_sessions = {}  # type: dict[str, OidcSession]


class Authenticator:
    """A module that logs a user into a bridge."""

    def __init__(self, config: dict, api: ModuleApi):
        """Initialize a new instance.

        Args:
            config (dict):
                The values obtained from `homeserver.yaml` for this module.
            api:
                An instance of `synapse.module_api.ModuleApi`
                that enables this module to communicate with Synapse.
        """
        Config.model_validate(config)
        self.api = api

        self.api.register_spam_checker_callbacks(
            check_login_for_spam=self.check_login_for_spam,
        )

        self.enabled = config["enabled"]
        self.idp_id = config["idp_id"]
        self.bot_user_id = config["bot_user_id"]
        self.bridge_base_url = config["bridge_base_url"]
        self.token = config["hs_token"].encode("ascii")
        self.hs = api._hs
        self.remote_user_id_field = config["remote_user_id_field"]

    async def check_login_for_spam(
        self,
        user_id: str,
        device_id: Optional[str],
        initial_display_name: Optional[str],
        request_info: Collection[Tuple[Optional[str], str]],
        auth_provider_id: Optional[str] = None,
    ) -> Union[NOT_SPAM, errors.Codes]:
        if not self.enabled:
            logger.info("Authenticator is disabled")
            oidc_sessions.clear()
            return NOT_SPAM

        if auth_provider_id != f"oidc-{self.idp_id}":
            logger.info("Ignoring login from another identity provider")
            return NOT_SPAM

        if user_id == self.bot_user_id:
            logger.info(f"Ignoring bot: {user_id}")
            return NOT_SPAM

        localpart = user_id.split(":")[0][1:]
        session = oidc_sessions.get(localpart)

        if not session:
            logger.warning(f"Session not found for {user_id}")
            return NOT_SPAM

        del oidc_sessions[localpart]

        uri = f"{self.bridge_base_url}/_connect/v2/users"
        post_json = {
            "user_mxid": user_id,
            "expires_at": session.expires_at,
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            self.remote_user_id_field: session.remote_user_id,
        }
        headers = {b"Authorization": [b"Bearer " + self.token]}
        await self.hs.get_simple_http_client().post_json_get_json(
            uri, post_json, headers=headers
        )

        return NOT_SPAM
