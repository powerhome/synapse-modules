"""Module for MentionInviteListener class."""

import logging
from typing import Any, Awaitable, Callable, Collection, Optional

from synapse.module_api import EventBase, ModuleApi
from synapse.types import StateMap, UserID

from ..helpers.user import UserHelpers
from .audiences_membership_handler import AudiencesMembershipHandler

logger = logging.getLogger(__name__)

ACTIVE_MEMBERSHIPS = ("join", "invite")


class MentionInviteListener:
    """
    Adds the users a member @mentions in a public room, when they aren't in it.

    Any joined member may add someone to a public room by mentioning them
    (JEDI-1129). Explicit invites (`POST /invite`) stay admin-only; the mention
    is read from the sent message itself, so a client can't claim one.

    Messages relayed from V2 by the bridge are skipped: V2 adds those people
    with its own republish (JEDI-1130). The bridge sends as an appservice, so
    its events carry no device.

    Every check runs on the event and the room state Synapse hands the
    callback, so an ordinary message costs no extra reads and never reaches
    CPG. That matters: even a no-op extra_users call schedules a full room
    evaluation there (JEDI-973).
    """

    def __init__(
        self,
        api: ModuleApi,
        config: dict[str, Any],
        audiences: Optional[AudiencesMembershipHandler] = None,
        deactivated_among: Optional[
            Callable[[Collection[str]], Awaitable[set[str]]]
        ] = None,
    ):
        # on_new_event fires on every process that sees the event; only the
        # main process may act on it, or each mention is added once per worker.
        assert api.worker_name is None, "MentionInviteListener is main-process only"
        hs = api._hs
        self.api = api
        self.bot_user_ids = set(config["bot_user_ids"])
        self.audiences = audiences or AudiencesMembershipHandler(
            hs, config["hs_token"], f"oidc-{config['idp_id']}"
        )
        self.deactivated_among = deactivated_among or (
            lambda user_ids: UserHelpers.get_deactivated_among(
                hs.get_datastores().main.db_pool, user_ids
            )
        )

    def register_third_party_rules_callbacks(self) -> None:
        self.api.register_third_party_rules_callbacks(on_new_event=self.on_new_event)

    async def on_new_event(self, event: EventBase, state_events: StateMap[EventBase]):
        candidates = self._candidates(event, state_events)
        if not candidates:
            return

        # Synapse awaits this callback while notifying clients of the event,
        # so the CPG round trip runs in the background.
        self.api.run_as_background_process(
            "mention_invite", self._add_to_room, event.room_id, candidates
        )

    def _candidates(
        self, event: EventBase, state_events: StateMap[EventBase]
    ) -> list[str]:
        if not self._is_original_message(event):
            return []

        # Absent rather than None when unset, as Synapse reads it too.
        if getattr(event.internal_metadata, "device_id", None) is None:
            return []

        if not self._is_public(state_events):
            return []

        if self._membership(state_events, event.sender) != "join":
            return []

        return [
            user_id
            for user_id in dict.fromkeys(self._mentioned_user_ids(event))
            if user_id != event.sender
            and user_id not in self.bot_user_ids
            and self._membership(state_events, user_id) not in ACTIVE_MEMBERSHIPS
        ]

    async def _add_to_room(self, room_id: str, candidates: list[str]) -> None:
        deactivated = await self.deactivated_among(candidates)
        users = [
            UserID.from_string(user_id)
            for user_id in candidates
            if user_id not in deactivated
        ]
        if not users:
            return

        try:
            await self.audiences.update(room_id, users, "invite")
        except Exception:
            logger.exception(
                "Could not add mentioned users to room %s: %s",
                room_id,
                [user.to_string() for user in users],
            )

    @staticmethod
    def _is_original_message(event: EventBase) -> bool:
        if event.type != "m.room.message" or event.is_state():
            return False

        relates_to = event.content.get("m.relates_to") or {}
        return relates_to.get("rel_type") != "m.replace"

    @staticmethod
    def _mentioned_user_ids(event: EventBase) -> list[str]:
        mentions = event.content.get("m.mentions")
        user_ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
        if not isinstance(user_ids, list):
            return []
        # Client-controlled: drop anything that isn't a user id here, so one
        # malformed entry can't fail the add for the rest or take a cap slot.
        return [
            user_id
            for user_id in user_ids
            if isinstance(user_id, str) and UserID.is_valid(user_id)
        ]

    @staticmethod
    def _is_public(state_events: StateMap[EventBase]) -> bool:
        join_rules = state_events.get(("m.room.join_rules", ""))
        return (
            join_rules is not None and join_rules.content.get("join_rule") == "public"
        )

    @staticmethod
    def _membership(state_events: StateMap[EventBase], user_id: str) -> Optional[str]:
        member = state_events.get(("m.room.member", user_id))
        return member.membership if member is not None else None
