"""Custom auth validator module with token validation and diagnostics."""

import logging
import traceback
from typing import Collection, Optional, Tuple, Union

import pymacaroons
import unpaddedbase64
from pymacaroons.exceptions import MacaroonDeserializationException
from synapse.api.auth.internal import InternalAuth
from synapse.api.errors import InvalidClientTokenError
from synapse.module_api import NOT_SPAM, ModuleApi
from synapse.types import Requester

logger = logging.getLogger(__name__)


class AuthValidator:
    """A module that validates tokens and provides diagnostics for auth issues."""

    def __init__(self, config: dict, api: ModuleApi):
        """Initialize a new instance.

        Args:
            config: The values obtained from `homeserver.yaml` for this module.
            api: An instance of `synapse.module_api.ModuleApi` that enables this module to communicate with Synapse.
        """
        self.api = api

        self.api.register_spam_checker_callbacks(
            check_login_for_spam=self.check_login_for_spam,
        )

        self._hs = api._hs
        self._auth = self._hs.get_auth()
        self._store = self._hs.get_datastores().main
        self._macaroon_generator = self._auth._macaroon_generator

        self._original_get_user_by_access_token = InternalAuth.get_user_by_access_token

        async def patched_get_user_by_access_token(
            auth_self, token: str, allow_expired: bool = False
        ) -> Requester:
            return await self._get_user_by_access_token_with_diagnostics(
                auth_self, token, allow_expired
            )

        InternalAuth.get_user_by_access_token = patched_get_user_by_access_token

    async def check_login_for_spam(
        self,
        user_id: str,
        device_id: Optional[str],
        initial_display_name: Optional[str],
        request_info: Collection[Tuple[Optional[str], str]],
        auth_provider_id: Optional[str] = None,
    ) -> Union[NOT_SPAM, str]:
        """Check login for spam and run diagnostics if enabled.

        Args:
            user_id: User ID attempting to log in
            device_id: Device ID for the login
            initial_display_name: Initial display name
            request_info: Request information tuples
            auth_provider_id: Authentication provider ID

        Returns:
            NOT_SPAM if login is valid, error code if invalid
        """
        await self._run_user_diagnostics(user_id)

        return NOT_SPAM

    def _get_token_preview(self, token: str) -> str:
        """Get a safe preview of the token for logging.

        Args:
            token: The token to preview

        Returns:
            Safe preview string showing first 10 characters
        """
        return token[:10] if len(token) > 10 else token

    async def _run_user_diagnostics(self, user_id: str):
        """Run diagnostics on the user to understand auth patterns.

        Args:
            user_id: The user ID attempting to log in
        """
        try:
            user_info = await self._store.get_user_by_id(user_id)
            if user_info:
                logger.info(
                    "User %s found in database (is_guest: %s, is_deactivated: %s)",
                    user_id,
                    user_info.is_guest,
                    user_info.is_deactivated,
                )
            else:
                logger.warning("User %s not found in database", user_id)

        except Exception as e:
            logger.error(
                "Unexpected error running user diagnostics: %s\\n%s",
                e,
                traceback.format_exc(),
            )

    async def _run_diagnostics(self, token: str):
        """Run diagnostics on the token to understand auth issues.

        Args:
            token: The access token to diagnose
        """
        try:
            user_info = await self._store.get_user_by_access_token(token)
            if user_info:
                logger.info(
                    "Token found in database for user %s (is_guest: %s)",
                    user_info.user_id,
                    user_info.is_guest,
                )
                return

            access_token_prefix = "syt_"  # noqa: S105
            if token.startswith(access_token_prefix):
                user_id = self._extract_user_id_from_token(token)
                if user_id:
                    logger.warning(
                        "Invalid or expired access token for user %s", user_id
                    )
                else:
                    logger.warning("Invalid or expired access token")
                return

            logger.info(
                f"Token does not start with {access_token_prefix}. Proceeding to macaroon diagnostics."
            )

            await self._diagnose_macaroon_token(token)

        except Exception as e:
            logger.error(
                "Unexpected error running diagnostics: %s\n%s",
                e,
                traceback.format_exc(),
            )

    def _extract_user_id_from_token(self, token: str) -> Optional[str]:
        """Extract user_id from a Synapse access token.

        Synapse access tokens have the format: syt_<base64_localpart>_<random>_<crc>
        This extracts and decodes the localpart to get the username.

        Args:
            token: The access token to extract from

        Returns:
            The user_id if extraction succeeds, None otherwise
        """
        try:
            if not token.startswith("syt_"):
                return None

            parts = token.split("_")
            if len(parts) < 2:
                return None

            base64_localpart = parts[1]
            localpart = unpaddedbase64.decode_base64(base64_localpart).decode("utf-8")

            server_name = self._hs.hostname
            user_id = f"@{localpart}:{server_name}"

            return user_id
        except Exception as e:
            logger.debug("Failed to extract user_id from token: %s", type(e).__name__)
            return None

    def _try_deserialize_macaroon(self, token: str):
        """Attempt to deserialize a macaroon token with detailed error logging.

        Args:
            token: The access token to deserialize

        Returns:
            Macaroon object if successful, None if deserialization fails
        """
        try:
            macaroon = pymacaroons.Macaroon.deserialize(token)
            logger.info(
                "Successfully deserialized macaroon. Location: %s, Identifier: %s",
                macaroon.location,
                macaroon.identifier,
            )
            return macaroon
        except MacaroonDeserializationException as e:
            error_msg = str(e)
            logger.warning(
                "MacaroonDeserializationException: %s\n"
                "Token (first 10 chars): %s...\n"
                "Token (last 10 chars): ...%s",
                error_msg,
                self._get_token_preview(token),
                token[-10:] if len(token) > 10 else "",
            )

            return None
        except Exception as e:
            logger.error(
                "Unexpected exception deserializing macaroon: %s",
                e,
            )
            return None

    async def _diagnose_macaroon_token(self, token: str):
        """Diagnose issues with macaroon token verification.

        Args:
            token: The access token to diagnose
        """
        try:
            macaroon = self._try_deserialize_macaroon(token)
            if macaroon is None:
                return

        except Exception as e:
            logger.error(
                "Unexpected error in macaroon diagnostics: %s\n%s",
                e,
                traceback.format_exc(),
            )

    async def _get_user_by_access_token_with_diagnostics(
        self,
        auth_self: InternalAuth,
        token: str,
        allow_expired: bool = False,
    ) -> Requester:
        if not token:
            raise InvalidClientTokenError("Empty access token")

        try:
            return await self._original_get_user_by_access_token(
                auth_self, token, allow_expired
            )
        except InvalidClientTokenError:
            try:
                await self._run_diagnostics(token)
            except Exception as diag_e:
                logger.error("Error running diagnostics: %s", diag_e)
            raise
