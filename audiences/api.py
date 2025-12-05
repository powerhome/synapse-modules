"""Audiences API."""

import logging
import re
import urllib.parse
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple, cast

from synapse.api.errors import Codes, HttpResponseException, SynapseError
from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet, parse_string_from_args
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.types import JsonDict, UserID

from ..archive_rooms.store import ArchiveRoomStore
from .audiences_auth import AudiencesAuth
from .audiences_batch_membership_processor import AudiencesBatchMembershipProcessor
from .power_levels_helpers import PowerLevelsHelpers as PowerLevels

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class AudiencesResource(JsonResource):
    def __init__(self, hs: "HomeServer", config: dict):
        module_api = hs.get_module_api()
        assert (
            module_api.worker_name is not None
        ), "AudiencesResource should only be used on the worker process"
        JsonResource.__init__(self, hs, canonical_json=False)

        self.register_servlets(self, hs, config)

    @staticmethod
    def register_servlets(resource: HttpServer, hs: "HomeServer", config: dict) -> None:
        AudiencesUserServlet(hs, config).register(resource)
        AudiencesScimServlet(hs, config).register(resource)
        AudiencesCreateRoomServlet(hs, config).register(resource)
        AudiencesContextServlet(hs, config).register(resource)
        AudiencesBatchMembershipProcessorServlet(hs, config).register(resource)
        AudiencesUsersServlet(hs, config).register(resource)


class AudiencesServlet(RestServlet):
    def __init__(self, hs: "HomeServer", config: dict):
        super().__init__()
        self.auth = hs.get_auth()
        self.hs = hs
        self.token = config["hs_token"]
        self.headers = {b"Authorization": [b"Bearer " + self.token.encode("ascii")]}

        self.idp_id = f"oidc-{config['idp_id']}" if "idp_id" in config else None
        self.db_pool = hs.datastores.main.db_pool

    def _uri(self, request: SynapseRequest) -> str:
        """
        Gets the URI of the request.

        Args:
            request (SynapseRequest): The request to get the URI of.

        Returns:
            str: The URI of the request.
        """
        return f"http://audiences:3000{request.uri.decode()}"

    async def _find_room_from_context(self, context_key: str) -> str:
        """
        Finds the room mxid from the context key.

        Args:
            context_key (str): The context key to find the room mxid from.

        Returns:
            str: The room mxid associated with the context key.

        Raises:
            to_synapse_error: If the room mxid is not found for the context key.
        """
        try:
            body = await self.hs.get_simple_http_client().get_json(
                uri=f"http://audiences:3000/audiences/api/rooms?context_key={context_key}",
                headers=self.headers,
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        return body[0]["mxid"]

    async def _replace_username(self, extra_users_json: JsonDict):
        scim_ids = {json["id"] for json in extra_users_json}
        mxids_by_scim_ids = dict(
            await self.db_pool.simple_select_many_batch(
                "user_external_ids",
                column="external_id",
                iterable=list(scim_ids),
                retcols=("external_id", "user_id"),
                keyvalues={"auth_provider": self.idp_id},
            )
        )
        for json in extra_users_json:
            scim_id = json["id"]
            if scim_id not in mxids_by_scim_ids:
                logger.warning(
                    f"SCIM ID {scim_id} not found in database mapping. Does this user exist on v3?"
                )
                continue

            mxid = mxids_by_scim_ids[scim_id]
            localpart = UserID.from_string(mxid).localpart
            json["userName"] = localpart


class AudiencesCreateRoomServlet(AudiencesServlet):
    PATTERNS = [re.compile("^/audiences/api/rooms")]
    CATEGORY = "Audience Create Room requests"

    def __init__(self, hs: "HomeServer", config: dict):
        super().__init__(hs, config)
        self.module_api = hs.get_module_api()
        self.bots = config.get("bot_user_ids", [])
        self.power_levels = PowerLevels(self.module_api)

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)
        if requester.user.localpart != "connect_bot":
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                f"{requester.user.localpart} must use the POST endpoint instead",
                Codes.FORBIDDEN,
            )

        try:
            body = await self.hs.get_simple_http_client().get_json(
                self._uri(request), headers=self.headers
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        await self._replace_username(body["context"]["extra_users"])

        return HTTPStatus.OK, body

    async def on_POST(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)
        user_id = requester.user.to_string()
        content = parse_json_object_from_request(request)
        try:
            room_id = content["room"]["mxid"]
        except KeyError:
            raise SynapseError(400, "Room ID not found in request")

        power_levels = await self.power_levels.get(room_id)
        await self.power_levels.authorize(user_id, power_levels)

        try:
            body = await self.hs.get_simple_http_client().post_json_get_json(
                self._uri(request),
                post_json=content,
                headers=self.headers,
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        await self._replace_username(body["context"]["extra_users"])

        return HTTPStatus.OK, body


class AudiencesUsersServlet(AudiencesServlet):
    PATTERNS = [
        re.compile("^/audiences/(?P<key>[^/]*)/users(/[^/]*)?/?$"),
    ]
    CATEGORY = "Audience context and criteria users requests"

    def __init__(self, hs: "HomeServer", config: dict):
        super().__init__(hs, config)
        self.module_api = hs.get_module_api()
        self.power_levels = PowerLevels(self.module_api)

    async def on_GET(self, request: SynapseRequest, key: str) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)
        room_mxid = await self._find_room_from_context(key)
        await self.power_levels.verify(requester, room_mxid)

        try:
            body = await self.hs.get_simple_http_client().get_json(
                uri=self._uri(request), headers=self.headers
            )

            return HTTPStatus.OK, body
        except HttpResponseException as e:
            raise e.to_synapse_error() from e


class AudiencesContextServlet(AudiencesServlet):
    PATTERNS = [
        re.compile("^/audiences/(?P<key>[^/]*)$"),
        re.compile("^/audiences/api/contexts/(?P<key>[^/]*)$"),
    ]
    CATEGORY = "Audience context requests"

    def __init__(self, hs: "HomeServer", config: dict):
        super().__init__(hs, config)
        self.module_api = hs.get_module_api()
        self.bots = config.get("bot_user_ids", [])
        self.power_levels = PowerLevels(self.module_api)
        self.audiences_auth = AudiencesAuth(self.hs, self.bots)
        self.audience_context_updated_event_type = config[
            "audience_context_updated_event_type"
        ]

    async def on_GET(self, request: SynapseRequest, key: str) -> Tuple[int, JsonDict]:
        room_mxid = await self._find_room_from_context(key)
        requester = await self.auth.get_user_by_req(request)
        await self.power_levels.verify(requester, room_mxid)

        try:
            body = await self.hs.get_simple_http_client().get_json(
                uri=self._uri(request), headers=self.headers
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        await self._replace_username(body["extra_users"])

        return HTTPStatus.OK, body

    async def on_PUT(self, request: SynapseRequest, key: str) -> Tuple[int, JsonDict]:
        requester = await self.auth.get_user_by_req(request)
        room_mxid = await self._find_room_from_context(key)
        await self.power_levels.verify(requester, room_mxid)

        content = parse_json_object_from_request(request)

        current_extra_users_json = await self._get_extra_users_json(room_mxid)
        updated_extra_users_json = content.get("extra_users", [])
        await self.audiences_auth.raise_if_all_human_admins_removed_after_update(
            room_mxid, current_extra_users_json, updated_extra_users_json
        )

        try:
            body = await self.hs.get_simple_http_client().put_json(
                uri=self._uri(request),
                json_body=content,
                headers=self.headers,
            )

            sender = requester.user.to_string()
            if sender in self.bots:
                return HTTPStatus.OK, body
            else:
                event_dict: JsonDict = {
                    "type": self.audience_context_updated_event_type,
                    "room_id": room_mxid,
                    "sender": sender,
                    "state_key": "",
                    "content": {
                        "key": key,
                        "origin": self.hs.hostname,
                        "timestamp": self.hs.get_clock().time_msec(),
                    },
                }
                await self.module_api.create_and_send_event_into_room(event_dict)
                return HTTPStatus.OK, body
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

    async def _get_extra_users_json(self, room_mxid: str) -> list[dict[str, str]]:
        try:
            json = await self.hs.get_simple_http_client().get_json(
                f"http://audiences:3000/audiences/api/rooms/{room_mxid}",
                headers=self.headers,
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        return json.get("context", {}).get("extra_users", [])


class AudiencesScimServlet(AudiencesServlet):
    PATTERNS = [re.compile("^/audiences/scim")]
    CATEGORY = "Audience SCIM requests"

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        await self.auth.get_user_by_req(request)

        # TODO: Authorization based on user's permissions. This should probably
        # happen in the identity provider, and we should provide a specific OAuth access token
        # for the user.

        try:
            body = await self.hs.get_simple_http_client().get_json(
                uri=self._uri(request), headers=self.headers
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        return HTTPStatus.OK, body


class AudiencesUserServlet(AudiencesServlet):
    PATTERNS = [re.compile("^/audiences/scim/Users")]
    CATEGORY = "Audience user requests"

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        await self.auth.get_user_by_req(request)

        if not self.idp_id:
            return await self._use_passthrough(request)

        parsed = parse_string_from_args(request.args, "filter", required=False)
        if not parsed:
            return await self._use_passthrough(request)

        parsed = cast(str, parsed)
        if parsed.count(" eq ") != 1:
            return await self._use_passthrough(request)

        match = re.search(r'userName eq "([^"]+)"', parsed)
        if not match:
            return await self._use_passthrough(request)

        localpart = match.group(1)
        return await self._get_scim_user(localpart)

    async def _get_scim_user(self, localpart: str) -> Tuple[int, JsonDict]:
        scim_id = await self._get_scim_id(localpart)
        try:
            query = urllib.parse.quote(f'id eq "{scim_id}"')
            uri = f"http://audiences:3000/audiences/scim/Users?filter={query}"
            body = await self.hs.get_simple_http_client().get_json(
                uri=uri, headers=self.headers
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        if body:
            assert len(body) == 1
            return HTTPStatus.OK, body
        else:
            raise SynapseError(
                HTTPStatus.NOT_FOUND, f"SCIM ID {scim_id} not found", Codes.NOT_FOUND
            )

    async def _get_scim_id(self, localpart: str) -> str:
        mxid = UserID(localpart, self.hs.hostname).to_string()
        return await self.db_pool.simple_select_one_onecol(
            "user_external_ids",
            keyvalues={"auth_provider": self.idp_id, "user_id": mxid},
            retcol="external_id",
            allow_none=False,
        )

    async def _use_passthrough(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        logger.info(
            "Using passthrough for GET /audiences/scim/Users request because filter does not contain exactly one user"
        )
        try:
            body = await self.hs.get_simple_http_client().get_json(
                uri=self._uri(request), headers=self.headers
            )
        except HttpResponseException as e:
            raise e.to_synapse_error() from e

        return HTTPStatus.OK, body


class AudiencesBatchMembershipProcessorServlet(AudiencesServlet):
    PATTERNS = [re.compile("^/audiences/api/changed$")]
    CATEGORY = [
        "Receives requests from the audiences service about changes to audiences."
    ]

    def __init__(self, hs: "HomeServer", config: dict):
        super().__init__(hs, config)
        self.config = config
        self.module_api = hs.get_module_api()
        self.bots = config.get("bot_user_ids", [])
        self.archive_room_store = ArchiveRoomStore(hs.get_datastores().main)

    async def on_PUT(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        """
        Notify Synapse about changes to audiences.

        This endpoint is used to notify Synapse about changes to audiences.

        Args:
            request (SynapseRequest): The request containing the room ID and sender.

        Returns:
            Tuple[int, JsonDict]: The HTTP status code and an empty JSON object.

        Raises:
            SynapseError: If the requester is not authorized to use this endpoint.
        """
        requester = await self.auth.get_user_by_req(request)
        if requester.user.localpart != "audiences_bot":
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                f"{requester.user.localpart} is unauthorized to use this endpoint",
                Codes.FORBIDDEN,
            )

        content = parse_json_object_from_request(request)
        room_id = content["room_id"]

        if await self.archive_room_store.is_archived(room_id):
            logging.info(
                f"Room {room_id}: Skipping batch membership processing for archived room"
            )
            return HTTPStatus.OK, {}

        handler = AudiencesBatchMembershipProcessor(self.config, self.module_api)
        self.module_api.run_as_background_process(
            "audiences_batch_processor",
            handler.process_batch_memberships,
            room_id,
        )

        return HTTPStatus.OK, {}
