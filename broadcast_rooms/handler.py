"""Broadcast rooms handler."""

from http import HTTPStatus
from typing import Tuple

from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict

from .store import BroadcastRoomStore


class BroadcastRoomHandler:
    """A handler for broadcast rooms."""

    def __init__(self, store: BroadcastRoomStore):
        self.store = store

    async def handle_all(self) -> Tuple[int, JsonDict]:
        return HTTPStatus.OK, await self.store.get_all_broadcasters()

    async def handle_get(self, room_id: str) -> Tuple[int, JsonDict]:
        broadcasters = await self.store.get_broadcasters(room_id)
        if broadcasters is None:
            return HTTPStatus.NOT_FOUND, {"error": f"{room_id} is not a broadcast room"}
        else:
            return HTTPStatus.OK, {"broadcasters": broadcasters}

    async def handle_put(self, room_id: str, content: JsonDict) -> Tuple[int, JsonDict]:
        broadcasters = self._parse_broadcasters(content)
        deduped_broadcasters = list(dict.fromkeys(broadcasters))
        await self.store.set_broadcasters(room_id, deduped_broadcasters)
        return HTTPStatus.OK, {"broadcasters": deduped_broadcasters}

    def _parse_broadcasters(self, content: JsonDict) -> list[str]:
        if "broadcasters" not in content:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "'broadcasters' key-value is missing",
                Codes.MISSING_PARAM,
            )

        broadcasters = content["broadcasters"]
        if not isinstance(broadcasters, list):
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "'broadcasters' is not a list",
                Codes.INVALID_PARAM,
            )

        if not all(isinstance(b, str) for b in broadcasters):
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"{broadcasters} contains non-strings",
                Codes.INVALID_PARAM,
            )

        return broadcasters

    async def handle_delete(self, room_id: str) -> Tuple[int, JsonDict]:
        await self.store.unbroadcast_room(room_id)
        return HTTPStatus.OK, {}
