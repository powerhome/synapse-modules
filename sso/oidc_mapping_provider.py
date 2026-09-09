"""Custom OIDC mapper for non-standard claims."""
import ast

import requests
from sqlalchemy.orm import Session
from synapse.handlers.oidc import (
    JinjaOidcMappingConfig,
    JinjaOidcMappingProvider,
    Token,
    UserAttributeDict,
    UserInfo,
)
from synapse.logging import logging
from synapse.module_api import ModuleApi
from synapse.types import JsonDict

from ..bridge import OidcSession, oidc_sessions
from ..db import Setup
from ..db.helpers import Helpers
from ..db.models import UserProfile

logger = logging.getLogger()
helper = Helpers()


class CustomOidcMappingProvider(JinjaOidcMappingProvider):
    """Custom OIDC mapping provider.

    Refer to the ID Token Claims section of the OIDC provider's documentation
    for details on the expected values.
    See synapse.handlers.oidc.JinjaOidcMappingProviders for additional details
    on functionality
    """

    def __init__(self, config: dict, module_api: ModuleApi) -> None:
        """See synapse.handlers.oidc.JinjaOidcMappingProviders.

        Args:
            config:
                The custom dict made by parse_config
            module_api:
                Synapse module API
        """
        super().__init__(config["jinja_config"], module_api)
        synapse_database = config["hs_config"]["synapse_server"]
        self.engine = Setup.create_engine(
            user=synapse_database["user"],
            password=synapse_database["password"],
            db=synapse_database["db"],
        )

    @staticmethod
    def parse_config(config: dict) -> dict:
        """

        Same as the base class's but will return a dict

        Args:
            config:
                values specified in the homeserver

        Returns:
            a dict where `hs_config` is the values from homeserver.yaml
            and `jinja_config` is the regular confg object. The `hs_config`
            is used to get custom values in this class
        """
        jinja_config = super(
            CustomOidcMappingProvider, CustomOidcMappingProvider
        ).parse_config(config)
        return {"hs_config": config, "jinja_config": jinja_config}

    async def map_user_attributes(
        self, userinfo: UserInfo, token: Token, failures: int
    ) -> UserAttributeDict:
        user_attributes = await super().map_user_attributes(userinfo, token, failures)
        localpart = user_attributes["localpart"]
        oidc_sessions[localpart] = OidcSession(
            expires_at=userinfo["exp"],
            access_token=token["access_token"],
            refresh_token=token["refresh_token"],
            remote_user_id=userinfo["employee_number"],
        )
        return user_attributes

    # https://docs.authlib.org/en/latest/specs/oidc.html?highlight=UserInfo
    async def get_extra_attributes(self, userinfo: UserInfo, token: Token) -> JsonDict:
        """

        See synapse.handlers.oidc.JinjaOidcMappingProviders.

        Args:
            userinfo:
                See JinjaOidcMappingProvider.get_extra_attributes
            token:
                See JinjaOidcMappingProvider.get_extra_attributes

        Returns:
            See JinjaOidcMappingProvider.get_extra_attributes
        """
        mapped_extras = await super().get_extra_attributes(userinfo, token)
        mapped_extras = self._add_avatar_url(mapped_extras)
        self._register_user_phone_numbers(mapped_extras, userinfo["email"])

        return mapped_extras

    def _create_username(self, email: str) -> str:
        """Takes a company email and extracts the username

        Args:
            email:
                Email string

        Returns:
            username
        """
        return email.split("@")[0]

    def _add_avatar_url(self, mapped_extras):
        """Adds avatar url to oidc response.

        The client needs to load the url and update it in
        a subsequent request since the url at this point will
        result in a `302` which breaks the media storage logic

        Args:
            mapped_extras:
                JinjaOidcMappingConfig's extra values as a dict

        Returns:
            mapped_extras with the avatar_url if present
        """
        keys = mapped_extras.keys()
        try:
            oidc_url = mapped_extras.get("avatar_url")
            if oidc_url:
                res = requests.get(oidc_url, timeout=10)
                res.raise_for_status()
                image_url = res.request.url
                mapped_extras["avatar_url"] = image_url
            else:
                logger.error(f"avatar_url not present from identity provider {keys}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Unable to make http request to {oidc_url}. {e}")
        return mapped_extras

    def _register_user_phone_numbers(
        self, mapped_extras: JinjaOidcMappingConfig, work_email: str
    ):
        """Writes a phone number from the oidc provider to the UserProfile table

        The user is authenticated by the oidc provider at this point so bad
        data should not be easy to create

        Args:
            mapped_extras:
                JinjaOidcMappingConfig's extra value object
            work_email:
                the work email is of the form `USERNAME@DOMAIN.com`
        """
        username = self._create_username(work_email)
        phone_number = self._get_one_phone_number(mapped_extras)
        if phone_number != "":
            with Session(self.engine) as session:
                helper.find_or_create_by(
                    session,
                    UserProfile,
                    phone_number=phone_number,
                    user_id=username,
                    through=["user_id"],
                )
        else:
            logger.warn("Unable to find phone number for username %s" % (username))

    def _get_one_phone_number(self, mapped_extras) -> str:
        """Returns a single phone number from a nested dict string extra values.

        Mapped extras is essentially a dict like

        ```python
        {
            'avatar_url': 'https://example.com/api/v1/users/USERID/photo/badge',
            'phone_numbers': "{'home': [], 'mobile': ['8888888888']}"
        }
        ```

        The following code locates the first number in `mobile` and then tries to look
        for any number in the `phone_numbers` dict

        Args:
            mapped_extras:
                JinjaOidcMappingConfig's extra value object

        Returns:
            10 digit phone number string or `""` if none found
        """  # noqa: E501, RST214, RST215, RST301, RST201
        phone_number_dict = ast.literal_eval(mapped_extras.get("phone_numbers"))
        if "mobile" in phone_number_dict and phone_number_dict["mobile"]:
            return phone_number_dict["mobile"][0]
        else:
            # in case phone_numbers keys change
            phone_number_types = list(phone_number_dict.keys())
            for number_type in phone_number_types:
                numbers = phone_number_dict[number_type]
                if numbers:
                    for number in numbers:
                        return number
        return ""
