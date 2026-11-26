"""Archive rooms handler."""

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Optional, Tuple

from synapse.api.constants import Membership
from synapse.api.errors import Codes, SynapseError, UnstableSpecAuthError
from synapse.module_api import ModuleApi
from synapse.types import JsonDict, Requester, create_requester

if TYPE_CHECKING:
    from synapse.server import HomeServer

from ..audiences.audiences_batch_membership_processor import (
    AudiencesBatchMembershipProcessor,
)
from ..rules.room_helpers import is_user_in_room
from ..v2_bridge import BridgeNotifier
from .store import ArchiveRoomStore

logger = logging.getLogger(__name__)


class ArchiveRoomHandler:
    """A handler for archiving rooms."""

    def __init__(
        self,
        hs: "HomeServer",
        api: ModuleApi,
        config: dict,
        bridge: Optional[BridgeNotifier] = None,
    ):
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
        self.bridge = bridge
        self.bridge_bot_mxid = (config.get("bridge") or {}).get("bot_mxid")
        self.audiences_bot_id = config.get("audiences_bot_user_id")

    async def handle_get(self, room_id: str) -> Tuple[int, JsonDict]:
        is_archived = await self.archive_store.is_archived(room_id)
        return HTTPStatus.OK, {"room_id": room_id, "archive": is_archived}

    async def handle_put(
        self, room_id: str, archive: bool, requester: Requester
    ) -> Tuple[int, JsonDict]:
        # Record who asked for the change before anything acts on it. The
        # members are removed by the audiences bot, so this is the only log
        # line that names the human who archived the room.
        logger.info(
            f"Room {'archive' if archive else 'unarchive'} requested for "
            f"{room_id} by {requester.user}"
        )

        # A room being blocked is our "source of truth" for archival
        # Prevents the room from being joinable
        if archive:
            # Never archive a self-DM. Archival parts and forgets the only
            # member, leaving an empty room that cannot be rejoined, so the user
            # permanently loses the ability to message themselves. The
            # auto-archive eligibility query already excludes these; this is a
            # defense-in-depth guard covering every caller (manual API included).
            if await self.archive_store.is_self_dm(room_id):
                logger.info(f"Refusing to archive self-DM room {room_id}")
                raise SynapseError(
                    HTTPStatus.BAD_REQUEST,
                    "Self-DM rooms cannot be archived",
                    Codes.FORBIDDEN,
                )
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

        self._notify_bridge(room_id, archive, requester)

        return HTTPStatus.OK, {"room_id": room_id, "archive": archive}

    def _notify_bridge(self, room_id: str, archive: bool, requester: Requester) -> None:
        """Tell Connect v2 about an archive status change, in the background.

        Skips changes made by the bridge's own bot to prevent an echo loop:
        when v2 archives a room, the bridge applies the change here as that bot,
        and re-notifying the bridge would push it straight back to v2. Every
        other actor -- humans and the audiences bot (which drives auto-archival)
        -- is a genuine v3-originated change that must reach v2. The bridge
        applies it in v2 as the Nitro bot; the requester is only logged.

        Args:
            room_id: The Matrix room ID whose archive status changed.
            archive: Whether the room was archived (True) or unarchived (False).
            requester: The user who initiated the archive status change.
        """
        if not self.bridge:
            return

        requester_mxid = str(requester.user)
        if requester_mxid == self.bridge_bot_mxid:
            logger.info(
                f"Not notifying bridge of archive change for {room_id}: "
                f"requester {requester_mxid} is the bridge bot "
                f"(change originated in v2)"
            )
            return

        self.bridge.archive_status_changed(room_id, archive, requester_mxid)

    async def _select_remover(
        self, room_id: str, requester: Requester, archive: bool
    ) -> Tuple[Requester, bool]:
        """Choose who removes the members of an archived room.

        Event auth stops a human from leaving a room when they are its only
        human admin, but it lets an admin bot kick them. So when archiving,
        the audiences bot removes everyone, including the human who asked for
        the archival, as long as the bot is joined to the room.

        If the bot is not joined (or is not configured), fall back to the
        requester removing the others. The requester cannot remove itself, so it
        stays in the room.

        Args:
            room_id: The room being archived or unarchived.
            requester: The user who initiated the change.
            archive: Whether the room is being archived (True) or unarchived.

        Returns:
            The requester to remove members as, and whether the original
            requester must be skipped because it is the one doing the removing.
        """
        bot_id = self.audiences_bot_id
        if not archive or not bot_id:
            return requester, True

        if await is_user_in_room(self.hs, bot_id, room_id):
            return create_requester(bot_id), False

        logger.warning(
            f"{bot_id} is not joined to {room_id}; {requester.user} will remove "
            f"the members and stay in the room"
        )
        return requester, True

    async def update_memberships_directly(
        self, room_id: str, requester: Requester, archive: bool
    ) -> None:
        remover, skip_requester = await self._select_remover(
            room_id, requester, archive
        )
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

                # A requester that is also doing the removing cannot remove itself
                if skip_requester and target_requester.user == requester.user:
                    continue

                logger.info(
                    f"{'Removing' if archive else 'Inviting'} {user_id} "
                    f"{'from' if archive else 'to'} {room_id} as {remover.user} "
                    f"(requested by {requester.user})"
                )

                try:
                    _, stream_id = await self.room_member_handler.update_membership(
                        requester=remover,
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
