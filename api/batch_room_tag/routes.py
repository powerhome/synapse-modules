"""Routes for batch_room_tag"""

import json
from typing import TYPE_CHECKING, Any, List

from psycopg2.errors import SerializationFailure
from pydantic import Field
from synapse.api.errors import MissingClientTokenError, SynapseError
from synapse.http.servlet import parse_and_validate_json_object_from_request
from synapse.http.site import SynapseRequest
from synapse.logging import logging
from synapse.module_api import ModuleApi
from synapse.types.rest import RequestBodyModel
from twisted.internet.defer import Deferred
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET
from typing_extensions import Annotated

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger()
ROOM_TAG_PATH = "/_connect/room_tags"


class RoomTagResource(Resource):
    """Resource for custom module."""

    def __init__(self, hs: "HomeServer"):
        super(RoomTagResource, self).__init__()
        self.hs = hs
        self.profile_handler = hs.get_profile_handler()
        self.auth = hs.get_auth()
        self.account_data_handler = hs.get_account_data_handler()

    class PutBody(RequestBodyModel):
        """synapse's pydantic model wrapper"""

        class Tag(RequestBodyModel):
            """pydantic model representation of json"""

            room_id: Annotated[str, Field(min_length=19, pattern=r"^\!\w+\:\w+")]
            tag_name: Annotated[str, Field(min_length=1)]
            content: Any

        tags: List[Tag]

    def _set_error(self, request, code, message):
        logger.error(message)
        request.setResponseCode(code)
        request.write(str.encode(json.dumps({"error": message})))

    async def _call_room_tag(self, request, tags, user_id) -> bool:
        try:
            for tag in tags:
                await self.account_data_handler.add_tag_to_room(
                    user_id, tag.room_id, tag.tag_name, tag.content
                )
                logger.debug(
                    f"{user_id} added {tag.tag_name} room tag to room {tag.room_id}"
                )
            return True
        except SerializationFailure:
            self._set_error(
                request,
                429,
                "Too many concurrent requests. Please try again later or to update fewer rooms.",
            )
            return False

    async def _delayed_write(self, request: SynapseRequest) -> bytes:
        """Just to get around async limitations of the render

        Args:
            request:
              request from the connect client
        """
        try:
            requester = await self.auth.get_user_by_req(request)
            request_json = parse_and_validate_json_object_from_request(
                request, self.PutBody
            )
            tags = request_json.tags
            room_ids = set(map(lambda tag: tag.room_id, tags))  # noqa: C417
            user_id = requester.user.to_string()
            result = await self._call_room_tag(request, tags, user_id)
            if result:
                logger.info(
                    f"User {user_id} added {len(tags)} room tags for rooms {room_ids}"
                )
                request.setResponseCode(200)
                request.write(
                    str.encode(json.dumps({"status": f"Saved {len(tags)} room tags"}))
                )
        except MissingClientTokenError:
            self._set_error(request, 401, "user unauthorized")
        except SynapseError as e:
            self._set_error(request, 422, e.msg)
        finally:
            request.finish()

    def render(self, request) -> bytes:  # noqa: N802
        """Render method used for twisted route

        Args:
            request:
              request from the connect client

        Returns:
            bytestring response
        """
        if request.method != b"PUT":
            request.setResponseCode(405)
            request.setHeader("Allow", "PUT")
            return str.encode(
                json.dumps(
                    {"error": f"{request.method.decode()} request not supported"}
                )
            )
        else:
            Deferred.fromCoroutine(self._delayed_write(request))
            return NOT_DONE_YET


class RoomTagRoutes:
    """Establishes connect.batch_room_tag routes

    See
    https://matrix-org.github.io/synapse/latest/modules/writing_a_module.html
    for more details
    """

    def __init__(self, config: dict, api: ModuleApi):
        api.register_web_resource(
            path=ROOM_TAG_PATH,
            resource=RoomTagResource(api._hs),
        )
