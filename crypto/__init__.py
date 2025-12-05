"""At-rest encryption module."""

import logging
import os
from typing import List, Optional, Sequence, Tuple, Union

from pydantic import Field, SecretStr
from synapse.appservice import (
    ApplicationService,
    TransactionOneTimeKeysCount,
    TransactionUnusedFallbackKeys,
)
from synapse.appservice.api import ApplicationServiceApi
from synapse.events import EventBase
from synapse.http.site import SynapseRequest
from synapse.module_api import ModuleApi
from synapse.push.httppusher import HttpPusher
from synapse.rest.client.room import (
    RoomInitialSyncRestServlet,
    RoomMessageListRestServlet,
    RoomSendEventRestServlet,
)
from synapse.rest.client.sync import SyncRestServlet
from synapse.types import DeviceListUpdates, JsonDict, JsonMapping

from ..base_config import BaseConfig
from ..notification_payloads.alert import Alert
from ..notification_payloads.types import EventTypeEnum, NotificationType
from .util import ConnectCryptographer


class Config(BaseConfig):
    """Configuration for crypto module."""

    key: SecretStr = Field(min_length=1)


logger = logging.getLogger(__name__)

cc = None


class Module:
    """A module that encrypts and decrypts messages."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        key = config["key"]
        global cc
        cc = ConnectCryptographer(key)


# region PUT /client/v3/rooms/{roomId}/send/{eventType}/{txnId}


class ConnectRoomSendEventRestServlet(RoomSendEventRestServlet):
    """
    A subclass that encrypts new messages created with the following endpoints:

    POST /client/v3/rooms/{roomId}/send/{eventType}
    PUT /client/v3/rooms/{roomId}/send/{eventType}/{txnId}
    """

    async def on_POST(
        self,
        request: SynapseRequest,
        room_id: str,
        event_type: str,
    ) -> Tuple[int, JsonDict]:
        cc.encrypt_request(request, event_type)
        return await super().on_POST(request, room_id, event_type)

    async def on_PUT(
        self, request: SynapseRequest, room_id: str, event_type: str, txn_id: str
    ) -> Tuple[int, JsonDict]:
        cc.encrypt_request(request, event_type)
        return await super().on_PUT(request, room_id, event_type, txn_id)


# endregion


# region GET /client/v3/rooms/{roomId}/messages


class ConnectRoomMessageListRestServlet(RoomMessageListRestServlet):
    """
    A subclass that decrypts messages fetched by the following endpoint:

    GET /client/v3/rooms/{roomId}/messages
    """

    async def on_GET(
        self, request: SynapseRequest, room_id: str
    ) -> Tuple[int, JsonDict]:
        (status, response) = await super().on_GET(request, room_id)
        if status != 200:
            return (status, response)

        for event in response.get("chunk", []):
            cc.decrypt_event(event)

        return (status, response)


# endregion


# region GET /client/v3/rooms/{roomId}/initialSync


class ConnectRoomInitialSyncRestServlet(RoomInitialSyncRestServlet):
    """
    A subclass that decrypts messages fetched by the following endpoint:

    GET /client/v3/rooms/{roomId}/initialSync
    """

    async def on_GET(
        self, request: SynapseRequest, room_id: str
    ) -> Tuple[int, JsonDict]:
        (status, response) = await super().on_GET(request, room_id)
        if status != 200:
            return (status, response)

        for event in response.get("messages", {}).get("chunk", []):
            cc.decrypt_event(event)

        return (status, response)


# endregion


# region GET /client/v3/sync


class ConnectSyncRestServlet(SyncRestServlet):
    """
    A subclass that decrypts messages fetched by the following endpoint:

    GET /client/v3/sync
    """

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        (status, response) = await super().on_GET(request)
        if status != 200:
            return (status, response)

        for joined_room in response.get("rooms", {}).get("join", {}).values():
            events = joined_room.get("timeline", {}).get("events", [])
            for event in events:
                cc.decrypt_event(event)

        for left_room in response.get("rooms", {}).get("leave", {}).values():
            events = left_room.get("timeline", {}).get("events", [])
            for event in events:
                cc.decrypt_event(event)

        return (status, response)


# endregion


# region POST /push/v1/notify


async def dispatch_push(
    self,
    content: JsonDict,
    tweaks: Optional[JsonMapping] = None,
    default_payload: Optional[JsonMapping] = None,
) -> Union[bool, List[str]]:
    assert cc is not None
    cc.decrypt_event(content)

    if NotificationType.get_event_type(content) == EventTypeEnum.ROOM_MEMBERSHIP:
        return []

    if "sender_display_name" in content:
        prefix = os.getenv("NOTIFICATION_ALERT_PREFIX", "")
        content["sender_raw_name"] = content["sender_display_name"]
        content["sender_display_name"] = f"{prefix}{content['sender_display_name']}"

    default_payload = Alert(content).apns_dict(default_payload)

    return await super_dispatch_push(
        self, content, tweaks=tweaks, default_payload=default_payload
    )


super_dispatch_push = HttpPusher.dispatch_push
HttpPusher.dispatch_push = dispatch_push


# endregion


# region PUT /app/v1/transactions/{txnId}


async def push_bulk(
    self,
    service: "ApplicationService",
    events: Sequence[EventBase],
    ephemeral: List[JsonMapping],
    to_device_messages: List[JsonMapping],
    one_time_keys_count: TransactionOneTimeKeysCount,
    unused_fallback_keys: TransactionUnusedFallbackKeys,
    device_list_summary: DeviceListUpdates,
    txn_id: Optional[int] = None,
) -> bool:
    for event in events:
        cc.decrypt_content(event.content)
    return await super_push_bulk(
        self,
        service,
        events,
        ephemeral,
        to_device_messages,
        one_time_keys_count,
        unused_fallback_keys,
        device_list_summary,
        txn_id=txn_id,
    )


super_push_bulk = ApplicationServiceApi.push_bulk
ApplicationServiceApi.push_bulk = push_bulk


# endregion
