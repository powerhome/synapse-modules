"""Monkey patches for event authorization."""

import collections.abc
import logging
from copy import deepcopy
from typing import TYPE_CHECKING, List, Optional, Tuple

from pydantic import BaseModel, Field
from synapse import event_auth
from synapse.api.constants import EventTypes, Membership
from synapse.api.errors import AuthError, Codes, SynapseError, UnstableSpecAuthError
from synapse.api.room_versions import RoomVersion
from synapse.module_api import ModuleApi
from synapse.types import StateMap, UserID

if TYPE_CHECKING:
    from synapse.events import EventBase

logger = logging.getLogger(__name__)

bot_ids = None


class Config(BaseModel):
    """Configuration for event auth monkey patches."""

    bot_user_ids: list[str] = Field(min_items=1, default_factory=list)


class EventAuthPatches:
    """A module that customizes Synapse event authorization."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        global bot_ids
        bot_ids = set(config.get("bot_user_ids", []))

        if api.worker_name is None:
            self.store = api._hs.get_datastores().main
            self.api = api
            # All processes call on_new_event. Unfortunately, that means each process would also
            # call create_and_send_event_into_room, which would create multiple identical
            # m.room.power_levels events. Synapse's worker locking behavior also makes this slow.
            # We work around this issue by only handling this on_new_event on the main process.
            self.api.register_third_party_rules_callbacks(
                on_new_event=self.on_new_event
            )

    async def on_new_event(
        self, event: "EventBase", state_events: StateMap["EventBase"]
    ):
        if event.type != EventTypes.Member:
            return

        if event.membership != Membership.LEAVE:
            return

        if power_levels := state_events.get(("m.room.power_levels", "")):
            await self._delete_user_from_power_levels(event, power_levels)

    async def _delete_user_from_power_levels(
        self, leave_event: "EventBase", power_levels: "EventBase"
    ):
        user = leave_event.state_key
        users = power_levels.content["users"]
        if user not in users:
            return

        if await self.store.is_room_blocked(leave_event.room_id):
            logger.info(
                f"Not demoting {user} because {leave_event.room_id} was archived"
            )
            return

        content = deepcopy(power_levels.content)
        del content["users"][user]

        sender = await self._find_sender_in_room(leave_event, content)

        await self.api.create_and_send_event_into_room(
            {
                "type": "m.room.power_levels",
                "content": content,
                "room_id": leave_event.room_id,
                "sender": sender,
                "state_key": "",
            }
        )
        logger.info(f"Deleted {user} from power levels")

    # In existing environments, the m.room.power_levels event's "users" property may contain admins
    # who are no longer room members.
    #
    # We expect audiences_bot to be a member (and admin) in every leave-able room, which makes it
    # the ideal user to update the power levels event when another admin leaves the room.
    #
    # In the unlikely case that the above is impossible, we try to use another admin present in the
    # room to send the event.
    async def _find_sender_in_room(
        self, leave_event: "EventBase", content: dict
    ) -> str:
        audiences_bot_id = f"@audiences_bot:{self.api.server_name}"

        audiences_bot_is_admin = content["users"].get(audiences_bot_id) == 100
        audiences_bot_in_room = await self.store.check_local_user_in_room(
            audiences_bot_id, leave_event.room_id
        )
        if audiences_bot_is_admin and audiences_bot_in_room:
            return audiences_bot_id

        logger.info(
            f"{audiences_bot_id} is not in {leave_event.room_id}, falling back to other admin"
        )

        admin_ids = [
            user_id
            for (user_id, power_level) in content["users"].items()
            if power_level == 100 and user_id != audiences_bot_id
        ]
        for admin_id in admin_ids:
            if await self.store.check_local_user_in_room(admin_id, leave_event.room_id):
                return admin_id

        return leave_event.sender


def _raise_if_human_demotes_bot_or_last_human(
    new_power_levels_event: "EventBase", current_power_levels_event: "EventBase"
):
    assert bot_ids is not None
    if new_power_levels_event.sender in bot_ids:
        return

    current_power_levels = current_power_levels_event.content.get("users", {}).items()
    current_admin_ids = {
        user_id for (user_id, power_level) in current_power_levels if power_level == 100
    }
    updated_power_levels = new_power_levels_event.content.get("users", {}).items()
    updated_admin_ids = {
        user_id for (user_id, power_level) in updated_power_levels if power_level == 100
    }

    removed_admin_ids = current_admin_ids - updated_admin_ids
    if removed_admin_ids & bot_ids:
        raise AuthError(403, "Human admins cannot demote bot admins")

    human_admin_ids = updated_admin_ids - bot_ids
    if not human_admin_ids:
        raise AuthError(403, "There'd be no human admins left in this room")


def _check_power_levels(
    room_version_obj: RoomVersion,
    event: "EventBase",
    auth_events: StateMap["EventBase"],
) -> None:
    # This method is identical to Synapse's original _check_power_levels method except for the following:
    # - We removed the block raising "You don't have permission to remove ops level equal to your own"
    #   to allow an admin to demote another admin.
    # - We added new, self-documented code under the "PATCHED" comment.

    user_list = event.content.get("users", {})
    # Validate users
    for k, v in user_list.items():
        try:
            UserID.from_string(k)
        except Exception:
            raise SynapseError(400, "Not a valid user_id: %s" % (k,))

        try:
            int(v)
        except Exception:
            raise SynapseError(400, "Not a valid power level: %s" % (v,))

    # Reject events with stringy power levels if required by room version
    if (
        event.type == EventTypes.PowerLevels
        and room_version_obj.enforce_int_power_levels
    ):
        for k, v in event.content.items():
            if k in {
                "users_default",
                "events_default",
                "state_default",
                "ban",
                "redact",
                "kick",
                "invite",
            }:
                if type(v) is not int:  # noqa: E721
                    raise SynapseError(400, f"{v!r} must be an integer.")
            if k in {"events", "notifications", "users"}:
                if not isinstance(v, collections.abc.Mapping) or not all(
                    type(v) is int for v in v.values()  # noqa: E721
                ):
                    raise SynapseError(
                        400,
                        f"{v!r} must be a dict wherein all the values are integers.",
                    )

    key = (event.type, event.state_key)
    current_state = auth_events.get(key)

    if not current_state:
        return

    # PATCHED
    _raise_if_human_demotes_bot_or_last_human(event, current_state)

    user_level = event_auth.get_user_power_level(event.user_id, auth_events)

    # Check other levels:
    levels_to_check: List[Tuple[str, Optional[str]]] = [
        ("users_default", None),
        ("events_default", None),
        ("state_default", None),
        ("ban", None),
        ("redact", None),
        ("kick", None),
        ("invite", None),
    ]

    old_list = current_state.content.get("users", {})
    for user in set(list(old_list) + list(user_list)):
        levels_to_check.append((user, "users"))

    old_list = current_state.content.get("events", {})
    new_list = event.content.get("events", {})
    for ev_id in set(list(old_list) + list(new_list)):
        levels_to_check.append((ev_id, "events"))

    # MSC2209 specifies these checks should also be done for the "notifications"
    # key.
    if room_version_obj.limit_notifications_power_levels:
        old_list = current_state.content.get("notifications", {})
        new_list = event.content.get("notifications", {})
        for ev_id in set(list(old_list) + list(new_list)):
            levels_to_check.append((ev_id, "notifications"))

    old_state = current_state.content
    new_state = event.content

    for level_to_check, dir in levels_to_check:
        old_loc = old_state
        new_loc = new_state
        if dir:
            old_loc = old_loc.get(dir, {})
            new_loc = new_loc.get(dir, {})

        if level_to_check in old_loc:
            old_level: Optional[int] = int(old_loc[level_to_check])
        else:
            old_level = None

        if level_to_check in new_loc:
            new_level: Optional[int] = int(new_loc[level_to_check])
        else:
            new_level = None

        if new_level is not None and old_level is not None:
            if new_level == old_level:
                continue

        # Check if the old and new levels are greater than the user level
        # (if defined)
        old_level_too_big = old_level is not None and old_level > user_level
        new_level_too_big = new_level is not None and new_level > user_level
        if old_level_too_big or new_level_too_big:
            raise AuthError(
                403, "You don't have permission to add ops level greater than your own"
            )


event_auth._check_power_levels = _check_power_levels


def _raise_if_human_kicks_bot(leave_event: "EventBase"):
    assert bot_ids is not None
    if leave_event.sender in bot_ids:
        return

    if leave_event.state_key in bot_ids:
        raise AuthError(403, "Human admins cannot kick bot admins")


def _raise_if_last_human_kicks_self(
    event: "EventBase", auth_events: StateMap["EventBase"]
):
    assert bot_ids is not None

    current_state = auth_events.get(("m.room.power_levels", ""))

    if not current_state:
        return

    if event.content.get("reason") == "Account deactivated":
        logger.info(
            f"Allowing admin to leave the room {event.room_id}, because {event.user_id} has been deactivated."
        )
        return

    current_power_levels = current_state.content.get("users", {}).items()
    other_human_admin_ids = {
        user_id
        for (user_id, power_level) in current_power_levels
        if (user_id != event.user_id)
        and (user_id not in bot_ids)
        and (power_level == 100)
    }
    if not other_human_admin_ids:
        raise AuthError(
            403,
            "You cannot leave the room, because you are the only admin in this room.",
        )


def _is_leave_allowed(event: "EventBase", auth_events: StateMap["EventBase"]):
    # We modify Synapse's original _is_membership_change_allowed method for "leave" events by replacing "<=" with "<"
    # in the following condition below:    user_level < kick_level or user_level <= target_level
    #
    # This change enables admins (who all have the same power level) to kick each other.
    #
    # We also added new, self-documented code under the other "PATCHED" comments.

    assert event.type == EventTypes.Member and event.membership == Membership.LEAVE

    # PATCHED
    _raise_if_human_kicks_bot(event)

    target_user_id = event.state_key

    # get info about the caller
    key = (EventTypes.Member, event.user_id)
    caller = auth_events.get(key)

    caller_in_room = caller and caller.membership == Membership.JOIN
    caller_invited = caller and caller.membership == Membership.INVITE

    # get info about the target
    key = (EventTypes.Member, target_user_id)
    target = auth_events.get(key)

    target_banned = target and target.membership == Membership.BAN

    user_level = event_auth.get_user_power_level(event.user_id, auth_events)
    target_level = event_auth.get_user_power_level(target_user_id, auth_events)

    ban_level = event_auth.get_named_level(auth_events, "ban", 50)

    # If the user has been invited, they are allowed to change their
    # membership event to leave
    if caller_invited and target_user_id == event.user_id:
        return

    if not caller_in_room:  # caller isn't joined
        raise UnstableSpecAuthError(
            403,
            "%s not in room %s." % (event.user_id, event.room_id),
            errcode=Codes.NOT_JOINED,
        )

    if target_banned and user_level < ban_level:
        raise UnstableSpecAuthError(
            403,
            "You cannot unban user %s." % (target_user_id,),
            errcode=Codes.INSUFFICIENT_POWER,
        )
    elif target_user_id != event.user_id:
        kick_level = event_auth.get_named_level(auth_events, "kick", 50)

        # PATCHED
        if user_level < kick_level or user_level < target_level:
            raise UnstableSpecAuthError(
                403,
                "You cannot kick user %s." % target_user_id,
                errcode=Codes.INSUFFICIENT_POWER,
            )
    # PATCHED
    else:
        _raise_if_last_human_kicks_self(event, auth_events)


def _is_membership_change_allowed(
    room_version: RoomVersion, event: "EventBase", auth_events: StateMap["EventBase"]
) -> None:
    """Patched version of _is_membership_change_allowed from synapse.

    See https://github.com/matrix-org/synapse/blob/be65a8ec0195955c15fdb179c9158b187638e39a/synapse/event_auth.py#L465
    for further details. This patch is to handle requests from bots and to allow them to make
    any changes to existing users in the room.

    The original function throws exceptions when users are not allowed to make changes to the
    membership of users in the room.

    Args:
        room_version:
            the room version.
        event:
            the event being checked.
        auth_events:
            the room state to check the events against.
    """
    assert bot_ids is not None

    target_user_id = event.state_key
    requester_user_id = event.user_id

    if not (
        event.type == EventTypes.Member
        and event.membership == Membership.LEAVE
        and requester_user_id in bot_ids
        and target_user_id != requester_user_id
    ):
        if event.type == EventTypes.Member and event.membership == Membership.LEAVE:
            _is_leave_allowed(event, auth_events)
            return

        _is_membership_change_allowed_original(room_version, event, auth_events)


_is_membership_change_allowed_original = event_auth._is_membership_change_allowed
event_auth._is_membership_change_allowed = _is_membership_change_allowed
