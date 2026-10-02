"""Archive rooms handler."""

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.constants import Membership
from synapse.api.errors import UnstableSpecAuthError
from synapse.module_api import ModuleApi
from synapse.types import JsonDict, Requester, create_requester

if TYPE_CHECKING:
    from synapse.server import HomeServer

from ..audiences.audiences_batch_membership_processor import (
    AudiencesBatchMembershipProcessor,
)
from .bridge_client import BridgeClient
from .store import ArchiveRoomStore

logger = logging.getLogger(__name__)


class ArchiveRoomHandler:
    """A handler for archiving rooms."""

    def __init__(self, hs: "HomeServer", api: ModuleApi, config: dict):
        self.hs = hs
        self.api = api
        self.bot_ids = config.get("bot_user_ids", [])
        self.config = config
        self.main_store = hs.get_datastores().main
        self.room_member_handler = hs.get_room_member_handler()
        self.replication = hs.get_replication_data_handler()
        self.archive_store = ArchiveRoomStore(self.main_store)
        self.audiences_services_enabled = config.get(
            "audiences_services_enabled", False
        )
        self.bridge_config = config.get("bridge")

    async def handle_get(self, room_id: str) -> Tuple[int, JsonDict]:
        is_archived = await self.archive_store.is_archived(room_id)
        return HTTPStatus.OK, {"room_id": room_id, "archive": is_archived}

    async def handle_put(
        self, room_id: str, archive: bool, requester: Requester
    ) -> Tuple[int, JsonDict]:
        # A room being blocked is our "source of truth" for archival
        # Prevents the room from being joinable
        if archive:
            await self.main_store.block_room(room_id, str(requester.user))
            await self.update_memberships_directly(room_id, requester, archive)
        else:
            await self.main_store.unblock_room(room_id)
            if self.audiences_services_enabled:
                self.api.run_as_background_process(
                    "audiences_batch_processor_restore_memberships_from_audience_criteria",
                    AudiencesBatchMembershipProcessor(
                        self.config, self.api
                    ).restore_memberships_from_audience_criteria,
                    room_id,
                )

        # Only modify visibility for public rooms
        # This will prevent the room from showing up in available public rooms to join on archival
        join_rules_state = await self.api.get_room_state(
            room_id, [("m.room.join_rules", None)]
        )
        join_rules = join_rules_state.get(("m.room.join_rules", ""))
        room_is_currently_public = (
            join_rules and join_rules.content["join_rule"] == "public"
        )
        if room_is_currently_public:
            # This does NOT affect join_rules, only the 'is_public' column for rooms
            await self.main_store.set_room_is_public(room_id, (not archive))

        await self._notify_bridge(room_id, archive, requester)

        return HTTPStatus.OK, {"room_id": room_id, "archive": archive}

    async def _notify_bridge(
        self, room_id: str, archive: bool, requester: Requester
    ) -> None:
        """Notify the Connect v2 bridge of an archive status change.

        Skips changes made by the bridge's own bot to prevent an echo loop:
        when v2 archives a room, the bridge applies the change here as that bot,
        and re-notifying the bridge would push it straight back to v2. Every
        other actor -- humans and the audiences bot (which drives auto-archival)
        -- is a genuine v3-originated change that must reach v2. A failure to
        reach the bridge must not fail the archive, since blocked_rooms is
        already the source of truth and the call is idempotent.

        Args:
            room_id: The Matrix room ID whose archive status changed.
            archive: Whether the room was archived (True) or unarchived (False).
            requester: The user who initiated the archive status change.
        """
        if not self.bridge_config:
            return

        requester_mxid = str(requester.user)
        if requester_mxid == self.bridge_config["bot_mxid"]:
            logger.info(
                f"Not notifying bridge of archive change for {room_id}: "
                f"requester {requester_mxid} is the bridge bot "
                f"(change originated in v2)"
            )
            return

        # This is a temporary solution before we sunset v2:
        # Connect v2 has no Audiences Bot concept, so we impersonate a superuser
        # when bridging Archival requests to v2. Always act as the configured
        # impersonator instead of the real requester when calling v2.
        impersonator_mxid = self.bridge_config["impersonator_mxid"]
        logger.info(
            f"Notifying bridge of archive change for {room_id} initiated by "
            f"{requester_mxid}, impersonating {impersonator_mxid}"
        )

        try:
            bridge_client = BridgeClient(self.api, self.bridge_config)
            await bridge_client.update_archive_status(
                room_id, impersonator_mxid, archive
            )
        except Exception as e:
            logger.exception(
                f"Failed to notify bridge of archive change for {room_id}: {e}"
            )

    async def update_memberships_directly(
        self, room_id: str, requester: Requester, archive: bool
    ) -> None:
        users = await self.main_store.get_local_users_related_to_room(room_id)
        for user_id, membership in users:
            if user_id in self.bot_ids:
                logger.info(f"{user_id} is a bot, not removing/adding in {room_id}")
                continue

            # Ensure we aren't adding back anyone who was kicked/banned, etc
            if membership in (Membership.JOIN, Membership.LEAVE, Membership.INVITE):
                target_requester = create_requester(
                    user_id, authenticated_entity=str(requester.user)
                )

                # Do not remove user requesting the archival
                if target_requester.user == requester.user:
                    continue

                try:
                    _, stream_id = await self.room_member_handler.update_membership(
                        requester=requester,
                        target=target_requester.user,
                        room_id=room_id,
                        action=Membership.LEAVE if archive else Membership.INVITE,
                        content={},
                        ratelimit=False,
                        require_consent=False,
                    )

                    await self.replication.wait_for_stream_position(
                        self.hs.config.worker.events_shard_config.get_instance(room_id),
                        "events",
                        stream_id,
                    )

                    if archive:
                        await self.room_member_handler.forget(
                            target_requester.user, room_id, do_not_schedule_purge=True
                        )
                # User already has the desired membership
                except UnstableSpecAuthError:
                    pass
