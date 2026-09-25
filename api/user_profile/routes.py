"""Routes for user profile"""

import json
from typing import TYPE_CHECKING, Tuple

from pydantic import SecretStr
from sqlalchemy.orm import Session
from synapse.api.errors import MissingClientTokenError
from synapse.http.site import SynapseRequest
from synapse.logging import logging
from synapse.module_api import ModuleApi
from synapse.types import UserID
from twisted.internet.defer import Deferred
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET

from ...base_config import BaseConfig
from ...db import Setup
from ...db.models import UserProfile

if TYPE_CHECKING:
    from synapse.server import HomeServer


class Server(BaseConfig):
    """Database server configuration."""

    user: SecretStr
    password: SecretStr
    db: SecretStr


class Config(BaseConfig):
    """Configuration for user profile module."""

    server: Server
    host: SecretStr


logger = logging.getLogger()


class UserProfileResource(Resource):
    """Resource for custom module."""

    def __init__(self, hs: "HomeServer", hs_config: dict):  # noqa: DAR101
        super(UserProfileResource, self).__init__()
        self.hs = hs
        self.profile_handler = hs.get_profile_handler()
        self.auth = hs.get_auth()
        self.host = hs_config["host"]

        self.engine = Setup.create_engine(**hs_config["server"])

    def render(self, request) -> bytes:
        """Render method used for twisted route

        Args:
            request:
              request from the connect client

        Returns:
            bytestring response
        """
        if request.method != b"GET":
            request.setResponseCode(405)
            request.setHeader("Allow", "GET")
            return str.encode(
                json.dumps(
                    {"error": f"{request.method.decode()} request not supported"}
                )
            )
        else:
            Deferred.fromCoroutine(self._get_profile_data(request))
            return NOT_DONE_YET

    def _set_error(self, request, code, message):
        logger.error(message)
        request.setResponseCode(code)
        request.write(str.encode(json.dumps({"error": message})))

    async def _get_profile_data(self, request: SynapseRequest):
        """Authenticated call to the database to retrieve user data

        Args:
            request:
                request from the connect client
        """  # noqa: DAR401, D202
        try:
            await self._check_auth(request)
            user_id, valid_id = self._extract_user_id(request)
            if valid_id:
                with Session(self.engine) as session:
                    profile = session.get(UserProfile, {"user_id": user_id})
                    if profile:
                        user_profile = {
                            "user_id": profile.user_id,
                            "phone_number": profile.phone_number,
                        }
                        request.setResponseCode(200)
                        request.write(
                            str.encode(json.dumps({"user_profile": user_profile}))
                        )
                    else:
                        self._set_error(
                            request,
                            404,
                            f"User id {user_id} not found in connect.user_profile table",
                        )
            else:
                self._set_error(request, 422, "Username in param not valid")
        except KeyError:
            self._set_error(
                request,
                422,
                "Problem with query params. Must include `user_id=EXAMPLE.NAME`",
            )
        except RuntimeError:
            self._set_error(request, 500, "Task yield error for request")
        finally:
            request.finish()

    async def _check_auth(self, request):
        """Checks if user authenticated

        Args:
            request:
                request from the connect client
        """
        try:
            await self.auth.get_user_by_req(request)
        except MissingClientTokenError:
            self._set_error(request, 401, "User unauthorized")

    def _extract_user_id(self, request) -> Tuple[str, bool]:
        """Gets user ID from url

        Args:
            request:
                request from the connect client

        Returns:
            Tuple of user_id from the URL and if it was valid
        """
        try:
            user_id = (request.args)[b"user_id"][0].decode("utf-8")
            full_user_id = ""
            if "@" in user_id:
                full_user_id = user_id
                user_id = user_id.replace("@", "").split(":")[0]
            else:
                full_user_id = f"@{user_id}:{self.host}"
            return user_id, UserID.is_valid(full_user_id)
        except UnicodeError:
            self._set_error(request, 500, "Error processing URL")


class UserProfileRoutes:
    """Establishes connect.user_profile routes

    See https://matrix-org.github.io/synapse/latest/modules/writing_a_module.html
    for more details
    """

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        api.register_web_resource(
            path="/_connect/user_profile", resource=UserProfileResource(api._hs, config)
        )
