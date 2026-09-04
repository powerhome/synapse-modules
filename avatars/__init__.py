"""Avatars module."""

import io
import json
import logging
from http import HTTPStatus

from pydantic import Field, PositiveFloat, SecretStr
from synapse.api.errors import SynapseError
from synapse.module_api import ModuleApi
from synapse.storage.database import LoggingTransaction
from synapse.types import UserID, create_requester
from twisted.internet import defer
from twisted.python.failure import Failure
from twisted.web.client import readBody
from twisted.web.http_headers import Headers
from twisted.web.iweb import IResponse

from ..base_config import BaseConfig


class Config(BaseConfig):
    """Configuration for avatars module."""

    requests_per_second: PositiveFloat
    shared_secret_auth_token: SecretStr = Field(min_length=1)


logger = logging.getLogger(__name__)


class Module:
    """A module that handles avatars."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        is_worker = api.worker_name is not None
        if is_worker:
            logger.info(f"Not initializing on worker: {api.worker_name}")
            return

        self.requests_per_second = config["requests_per_second"]
        self.token = config["shared_secret_auth_token"]
        self.api = api
        self.hs = api._hs
        self.db_pool = self.hs.get_datastores().main.db_pool

        delay_msec = 900000  # 15 minutes
        api.looping_background_call(self._handle_avatars, delay_msec)

    async def _handle_avatars(self):
        records = await self._fetch_users_with_unset_avatars()
        logger.info(f"Handling {len(records)} users")

        for localpart, photo_url in records:
            await self.api.sleep(
                1.0 / self.requests_per_second
            )  # to avoid media-repo's ratelimit

            try:
                data, headers = await self._download_photo(photo_url)
            except SynapseError as e:
                if e.msg != "Got error 404":
                    logger.error(f"Failed to download avatar for {localpart}: {e}")
                continue

            try:
                response = await self._upload_photo(data, headers)
            except Exception as e:
                logger.error(f"Failed to upload avatar for {localpart}: {e}")
                continue

            user_id = UserID(localpart, self.hs.hostname)
            await self._set_avatar(user_id, response)

    async def _fetch_users_with_unset_avatars(self) -> list[tuple[str, str]]:
        def select(txn: LoggingTransaction):
            txn.execute(
                """SELECT pp.user_id, photos->>'value' FROM profiles pp
                JOIN connect.profiles cp ON pp.user_id = cp.user_id AND pp.avatar_url IS NULL AND cp.active IS TRUE
                CROSS JOIN jsonb_array_elements(cp.data->'photos') photos WHERE photos->>'type' = 'photo'
                """
            )
            return [(r[0], r[1]) for r in txn]

        return await self.db_pool.runInteraction(
            "fetch_users_with_unset_avatars", select, db_autocommit=True
        )

    async def _download_photo(
        self, photo_url: str
    ) -> tuple[bytes, dict[bytes, list[bytes]]]:
        photo = io.BytesIO()
        (
            _content_length,
            headers,
            _uri,
            code,
        ) = await self.hs.get_simple_http_client().get_file(
            url=photo_url, output_stream=photo
        )
        assert code == HTTPStatus.OK
        return photo.getvalue(), headers

    async def _upload_photo(
        self, data: bytes, headers: dict[bytes, list[bytes]]
    ) -> IResponse:
        upload_url = "http://media-repo:8000/_matrix/media/v3/upload"

        upload_headers = Headers()
        upload_headers.addRawHeader("Authorization", f"Bearer {self.token}")
        upload_headers.addRawHeader("Host", "synapse:8008")
        upload_headers.setRawHeaders("Content-Type", headers[b"Content-Type"])

        response = await self.hs.get_simple_http_client().request(
            "POST", upload_url, data=data, headers=upload_headers
        )
        assert response.code == 200
        return response

    async def _set_avatar(self, user_id: UserID, response: IResponse):
        async def set_avatar_url(user_id: UserID, avatar_url: str):
            try:
                await self.hs.get_profile_handler().set_avatar_url(
                    user_id, create_requester(user_id), avatar_url
                )
            except SynapseError as e:
                logger.error(f"Failed to set avatar for {user_id.localpart}: {e}")

        def on_success(data: bytes, user_id: UserID):
            avatar_url = json.loads(data)["content_uri"]
            defer.ensureDeferred(set_avatar_url(user_id, avatar_url))

        def on_failure(e: Failure, user_id: UserID):
            logger.error(f"Failed to set avatar for {user_id.localpart}: {e.value}")

        deferred = readBody(response)
        deferred.addCallback(on_success, user_id=user_id)
        deferred.addErrback(on_failure, user_id=user_id)
