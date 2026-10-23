"""Module for BatchRoomMembershipProcessor class, handling custom Matrix events."""

import logging
from typing import Any, Dict

from pydantic import Field
from synapse.api.errors import LimitExceededError, UnstableSpecAuthError
from synapse.module_api import EventBase, ModuleApi

from connect.base_config import BaseConfig
from connect.custom_types import CUSTOM_EVENT_TYPE


class Config(BaseConfig):
    """Configuration for batch room membership processor."""

    bot_user_ids: list[str] = Field(min_items=1)
    batch_room_members_event_type: CUSTOM_EVENT_TYPE


class BatchRoomMembershipProcessor:
    """This class listens for a custom Matrix event and applies batch membership changes.

    The Matrix spec (and Synapse API) has two fundamental limitations related to room membership changes:
    1) In at least some cases, members cannot add another user to a room (they must instead invite them).
    2) The Matrix API only allows a single membership change per HTTP request.

    These lead to slow performance in cases where many users need to be added to or removed from a room.

    To improve performance when batch membership changes are applied, this class listens for a custom Matrix
    event that accepts a batch of user IDs (MXIDs) and applies membership changes for them, via the Synapse module_api
    which is significantly faster than the Matrix API (via HTTP requests).

    If the membership is "invite", this creates a new invite event which is processed by the
    InviteAutoAccepter.

    Attributes:
        bot_user_ids (list): A list of Matrix IDs corresponding to bot users.
        _api (ModuleApi): An instance of the Synapse ModuleApi.
    """

    def __init__(self, config: Dict[str, Any], api: ModuleApi):
        Config.model_validate(config)
        is_worker = api.worker_name is not None
        if is_worker:
            logging.info(
                f"Not initializing BatchRoomMembershipProcessor on worker: {api.worker_name}"
            )
            return

        logging.info("Initializing BatchRoomMembershipProcessor")

        self.bot_user_ids = config["bot_user_ids"]
        self.batch_room_members_event_type = config["batch_room_members_event_type"]
        self._api = api

        self._api.register_third_party_rules_callbacks(
            on_new_event=self.on_new_event,
        )

    async def _update_room_membership(self, mxid, event: EventBase):
        """
        Asynchronously updates the room membership for a given Matrix ID.

        This method updates the room membership for the user identified by `mxid`. It does this by calling
        the Synapse Module API's `update_room_membership` method with the bot's user ID as the sender, the
        provided `mxid` as the target, the room ID from the event, the invite metadata from the event content,
        and the membership action (e.g. "invite") from the event content.

        Args:
            mxid (str): The Matrix ID of the user whose room membership should be updated.
            event (EventBase): The event that triggered the update.
        """
        abridged_content = {
            k: event.content[k] for k in {"is_direct", "origin"} if k in event.content
        }
        await self._api.update_room_membership(
            sender=event.sender,
            target=mxid,
            room_id=event.room_id,
            content=abridged_content,
            new_membership=event.content["membership"],
        )

    async def _process_batch_memberships(self, event: EventBase):
        """
        Processes batches of membership changes.

        Args:
            event (EventBase): The event to process.
        """
        mxids = event.content.get("mxids", [])
        for mxid in mxids:
            try:
                await self._update_room_membership(mxid, event)
            except UnstableSpecAuthError as e:
                if "already in the room" in str(e.msg):
                    pass
            except LimitExceededError as e:
                if e.retry_after_ms is not None:
                    # .. See https://github.com/matrix-org/synapse/issues/6286#issuecomment-646944920 and
                    # .. https://github.com/matrix-org/synapse/pull/9648
                    logging.warning(
                        f"[BatchRoomMembershipProcessor]: {e.msg}. Is {event.sender} in the ratelimit_override table?"
                    )
                    time_to_sleep = e.retry_after_ms / 1000
                    await self._api.sleep(time_to_sleep)
                    await self._update_room_membership(mxid, event)

    async def on_new_event(self, event: EventBase, *args: Any) -> None:
        """
        Asynchronously handle a new event.

        This method is a callback that allows the module to hook into new events created on Synapse. It processes the
        event by calling the `_process_batch_memberships` method.

        Args:
            event (EventBase): The new event to handle.
            args (Any): Additional arguments.
        """
        if (
            event.is_state()
            and event.type == self.batch_room_members_event_type
            and event.sender in self.bot_user_ids
            and event.content.get("membership") in ["invite", "leave"]
        ):
            await self._process_batch_memberships(event)
