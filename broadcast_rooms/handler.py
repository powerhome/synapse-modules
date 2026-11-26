"""Broadcast rooms handler."""

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Optional, Tuple

from synapse.api.errors import Codes, SynapseError
from synapse.module_api import ModuleApi
from synapse.types import JsonDict, Requester

from ..people_conversations.store import PeopleConversationStore
from ..v2_bridge import BridgeNotifier
from .store import BroadcastRoomStore

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)

DEFAULT_BROADCAST_UPDATED_EVENT_TYPE = "com.powerhrg.broadcast.updated"


class BroadcastRoomHandler:
    """A handler for broadcast rooms."""

    def __init__(
        self,
        hs: "HomeServer",
        api: ModuleApi,
        store: BroadcastRoomStore,
        config: dict,
        bridge: Optional[BridgeNotifier] = None,
    ):
        self.hs = hs
        self.api = api
        self.store = store
        self.main_store = hs.get_datastores().main
        self.people_conversation_store = PeopleConversationStore(
            self.main_store.db_pool
        )
        self.allowed_localparts = config["allowed_localparts"]
        self.updated_event_type = config.get(
            "broadcast_updated_event_type", DEFAULT_BROADCAST_UPDATED_EVENT_TYPE
        )
        self.bridge = bridge

    async def handle_all(self) -> Tuple[int, JsonDict]:
        return HTTPStatus.OK, await self.store.get_all_broadcasters()

    async def handle_get(self, room_id: str) -> Tuple[int, JsonDict]:
        broadcasters = await self.store.get_broadcasters(room_id)
        if broadcasters is None:
            return HTTPStatus.NOT_FOUND, {"error": f"{room_id} is not a broadcast room"}
        else:
            return HTTPStatus.OK, {"broadcasters": broadcasters}

    async def handle_put(
        self, room_id: str, content: JsonDict, requester: Requester
    ) -> Tuple[int, JsonDict]:
        """Make the room a broadcast room with exactly the given broadcasters.

        The table is the source of truth, so it is written first and a store
        error fails the request. Everything after it (the state event that tells
        clients to re-read and, for a person's change, the Connect v2
        notification) is best effort and never fails the request.

        Naming a broadcaster never changes the room's membership. A broadcaster
        who is not in the room can post once someone adds them, like any member.

        The bridge re-sends the list on every v2 room update, changed or not, so
        a write that changes nothing has no side effects at all.

        A person must name at least one broadcaster. Connect v2 has no broadcast
        room without broadcasters, so it could not be told about one, and its
        next update of the room would overwrite it here. To stop broadcasting,
        the room is deleted from this endpoint instead.

        Args:
            room_id: The room to make a broadcast room.
            content: The request body, holding the `broadcasters` list.
            requester: The user making the request.

        Returns:
            The response status and body, with the stored broadcasters.

        Raises:
            SynapseError: 400 when a person sends no broadcasters.
        """
        broadcasters = list(dict.fromkeys(self._parse_broadcasters(content)))
        by_service = self._is_service(requester)

        if not by_service:
            if not broadcasters:
                logger.info(
                    f"Refusing broadcast change for {room_id}: no broadcasters given"
                )
                raise SynapseError(
                    HTTPStatus.BAD_REQUEST,
                    "A broadcast room needs at least one broadcaster",
                    Codes.INVALID_PARAM,
                )
            await self._raise_unless_room_can_broadcast(room_id)

        previous = await self.store.get_broadcasters(room_id)
        await self.store.set_broadcasters(room_id, broadcasters)

        if previous is not None and set(previous) == set(broadcasters):
            return HTTPStatus.OK, {"broadcasters": broadcasters}

        logger.info(
            f"{requester.user} set broadcasters for {room_id}: "
            f"{self._describe(previous)} -> {broadcasters}"
        )

        if not by_service:
            self._notify_bridge(room_id, broadcasters, requester)
        await self._emit_updated_event(room_id, requester)

        return HTTPStatus.OK, {"broadcasters": broadcasters}

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

    async def handle_delete(
        self, room_id: str, requester: Requester
    ) -> Tuple[int, JsonDict]:
        """Make the room a regular room again, dropping its broadcaster list.

        A person's change is sent to Connect v2 as an empty broadcaster list,
        which is how v2 represents a room that is not a broadcast room.

        Args:
            room_id: The room that stops being a broadcast room.
            requester: The user making the request.

        Returns:
            The response status and an empty body.
        """
        previous = await self.store.get_broadcasters(room_id)
        await self.store.unbroadcast_room(room_id)

        logger.info(
            f"{requester.user} turned off broadcasting for {room_id}: "
            f"{self._describe(previous)} -> not a broadcast room"
        )

        await self._emit_updated_event(room_id, requester)
        if not self._is_service(requester):
            self._notify_bridge(room_id, [], requester)

        return HTTPStatus.OK, {}

    def _is_service(self, requester: Requester) -> bool:
        # Whether the request comes from the bridge or the bot, not a person.
        return requester.user.localpart in self.allowed_localparts

    @staticmethod
    def _describe(broadcasters: Optional[list[str]]) -> str:
        return "not a broadcast room" if broadcasters is None else str(broadcasters)

    async def _raise_unless_room_can_broadcast(self, room_id: str) -> None:
        """Refuse a person making a broadcast room out of anything but a private room.

        Connect v2 only has private broadcast rooms and never reports
        broadcasters for a public room or a people conversation, so such a room
        could not be kept in sync. The bridge and the bot are not checked: what
        they send already exists in v2.

        Args:
            room_id: The room about to become a broadcast room.

        Raises:
            SynapseError: 403 when the room cannot be a broadcast room.
        """
        if await self.people_conversation_store.is_people_conversation(room_id):
            logger.info(
                f"Refusing broadcast change for {room_id}: it is a people conversation"
            )
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                "People conversations cannot be broadcast rooms",
                Codes.FORBIDDEN,
            )

        room_state = await self.api.get_room_state(
            room_id, [("m.room.join_rules", None)]
        )
        join_rules = room_state.get(("m.room.join_rules", ""))
        if join_rules and join_rules.content.get("join_rule") == "public":
            logger.info(f"Refusing broadcast change for {room_id}: the room is public")
            raise SynapseError(
                HTTPStatus.FORBIDDEN,
                "Only private rooms can be broadcast rooms",
                Codes.FORBIDDEN,
            )

    async def _emit_updated_event(self, room_id: str, requester: Requester) -> None:
        """Tell the room's clients that its broadcast settings changed.

        The event carries no list, only a pointer to re-read the endpoint. The
        timestamp keeps successive events distinct, since Synapse drops a state
        event whose content matches the current one. Unlike the audiences
        context event, this is sent for the bridge and the bot too: a change made
        in Connect v2 must reach v3 clients as well.

        Args:
            room_id: The room whose broadcast settings changed.
            requester: The user who made the change, and the sender of the event.
        """
        try:
            await self.api.create_and_send_event_into_room(
                {
                    "type": self.updated_event_type,
                    "room_id": room_id,
                    "sender": str(requester.user),
                    "state_key": "",
                    "content": {
                        "origin": self.hs.hostname,
                        "timestamp": self.hs.get_clock().time_msec(),
                    },
                }
            )
        except Exception as e:
            logger.exception(
                f"Failed to send {self.updated_event_type} into {room_id}: {e}"
            )

    def _notify_bridge(
        self, room_id: str, broadcasters: list[str], requester: Requester
    ) -> None:
        """Tell Connect v2 about a person's broadcast change, in the background.

        Only called for a person. The services in allowed_localparts are the
        bridge (its appservice sender and its bot): their changes came from v2,
        and sending them back would echo.

        Args:
            room_id: The room whose broadcasters changed.
            broadcasters: The room's broadcasters; empty when it stopped being a broadcast room.
            requester: The person who made the change.
        """
        if not self.bridge:
            return

        self.bridge.broadcasters_changed(room_id, broadcasters, str(requester.user))
