"""User deactivation monkey patches."""

import logging
from typing import Optional

from synapse.api.errors import SynapseError
from synapse.handlers.deactivate_account import DeactivateAccountHandler
from synapse.handlers.device import DeviceHandler
from synapse.types import Codes, Requester, UserID, create_requester

from ..people_conversations.store import PeopleConversationStore
from ..self_dm import get_self_dm_id

logger = logging.getLogger(__name__)


class DeactivateAccountPatch:
    """A class containing patches for the DeactivateAccountHandler"""

    def __init__(self, config: dict, api) -> None:
        pass


# Patched: Avoid erasing account data and thread subscription settings on deactivation
# This will preserve user settings and prevent
# things like self_dm room creation misbehaving
async def patched_deactivate_account(
    self,
    user_id: str,
    erase_data: bool,
    requester: Requester,
    id_server: Optional[str] = None,
    by_admin: bool = False,
) -> bool:
    # This can only be called on the main process.
    assert isinstance(self._device_handler, DeviceHandler)

    # Check if this user can be deactivated
    if not await self._third_party_rules.check_can_deactivate_user(user_id, by_admin):
        raise SynapseError(
            403, "Deactivation of this user is forbidden", Codes.FORBIDDEN
        )

    # FIXME: Theoretically there is a race here wherein user resets
    # password using threepid.

    # delete threepids first. We remove these from the IS so if this fails,
    # leave the user still active so they can try again.
    # Ideally we would prevent password resets and then do this in the
    # background thread.

    # This will be set to false if the identity server doesn't support
    # unbinding
    identity_server_supports_unbinding = True

    # Attempt to unbind any known bound threepids to this account from identity
    # server(s).
    bound_threepids = await self.store.user_get_bound_threepids(user_id)
    for medium, address in bound_threepids:
        try:
            result = await self._identity_handler.try_unbind_threepid(
                user_id, medium, address, id_server
            )
        except Exception:
            # Do we want this to be a fatal error or should we carry on?
            logger.exception("Failed to remove threepid from ID server")
            raise SynapseError(400, "Failed to remove threepid from ID server")

        identity_server_supports_unbinding &= result

    # Remove any local threepid associations for this account.
    local_threepids = await self.store.user_get_threepids(user_id)
    for local_threepid in local_threepids:
        await self._auth_handler.delete_local_threepid(
            user_id, local_threepid.medium, local_threepid.address
        )

    # delete any devices belonging to the user, which will also
    # delete corresponding access tokens.
    await self._device_handler.delete_all_devices_for_user(user_id)
    # then delete any remaining access tokens which weren't associated with
    # a device.
    await self._auth_handler.delete_access_tokens_for_user(user_id)

    await self.store.user_set_password_hash(user_id, None)

    # Most of the pushers will have been deleted when we logged out the
    # associated devices above, but we still need to delete pushers not
    # associated with devices, e.g. email pushers.
    await self.store.delete_all_pushers_for_user(user_id)

    # Add the user to a table of users pending deactivation (ie.
    # removal from all the rooms they're a member of)
    await self.store.add_user_pending_deactivation(user_id)

    # delete from user directory
    await self.user_directory_handler.handle_local_user_deactivated(user_id)

    # Mark the user as erased, if they asked for that
    if erase_data:
        user = UserID.from_string(user_id)
        # Remove avatar URL from this user
        await self._profile_handler.set_avatar_url(
            user, requester, "", by_admin, deactivation=True
        )
        # Remove displayname from this user
        await self._profile_handler.set_displayname(
            user, requester, "", by_admin, deactivation=True
        )

        logger.info("Marking %s as erased", user_id)
        await self.store.mark_user_erased(user_id)

    # Reject all pending invites and knocks for the user, so that the
    # user doesn't show up in the "invited" section of rooms' members list.
    await self._reject_pending_invites_and_knocks_for_user(user_id)

    # Remove all information on the user from the account_validity table.
    if self._account_validity_enabled:
        await self.store.delete_account_validity_for_user(user_id)

    # Mark the user as deactivated.
    await self.store.set_user_deactivated_status(user_id, True)

    # PATCHED: Do NOT remove account data
    # Remove account data (including ignored users and push rules).
    # await self.store.purge_account_data_for_user(user_id)

    # PATCHED: Do NOT remove thread subscription settings
    # Remove thread subscriptions for the user
    # await self.store.purge_thread_subscription_settings_for_user(user_id)

    # Delete any server-side backup keys
    await self.store.bulk_delete_backup_keys_and_versions_for_user(user_id)

    # Notify modules and start the room parting process.
    await self.notify_account_deactivated(user_id, by_admin=by_admin)

    return identity_server_supports_unbinding


# Patched: Do not erase members from private rooms
# Patched: Do not part from self-dm room
async def _patched_part_user(self, user_id: str) -> None:
    user = UserID.from_string(user_id)

    rooms_for_user = await self.store.get_rooms_for_user(user_id)
    requester = create_requester(user, authenticated_entity=self._server_name)
    should_erase = await self.store.is_user_erased(user_id)
    db_pool = self.hs.get_datastores().main.db_pool
    self_dm_room_id = await get_self_dm_id(self.hs.get_module_api(), user_id)

    for room_id in rooms_for_user:
        # PATCHED LOGIC
        # Do not part user from people-conversations
        # Do not part user from self-dm
        room_is_people_conversation = await PeopleConversationStore(
            db_pool
        ).is_people_conversation(room_id)
        room_is_self_dm = self_dm_room_id == room_id

        if (room_is_people_conversation or room_is_self_dm) and not should_erase:
            logger.info(
                f"Skipping parting user {user_id} from {'self-dm' if room_is_self_dm else 'people-conversation'} room: {room_id}"
            )
            continue

        logger.info("User parter parting %r from %r", user_id, room_id)
        try:
            # Before parting the user, redact all membership events if requested
            if should_erase:
                event_ids = await self.store.get_membership_event_ids_for_user(
                    user_id, room_id
                )
                for event_id in event_ids:
                    await self.store.expire_event(event_id)

            await self._room_member_handler.update_membership(
                requester,
                user,
                room_id,
                "leave",
                ratelimit=False,
                content={"reason": "Account deactivated"},
                require_consent=False,
            )

            # Mark the room forgotten too, because they won't be able to do this
            # for us. This may lead to the room being purged eventually.
            await self._room_member_handler.forget(user, room_id)
        except Exception:
            logger.exception(
                "Failed to part user %r from room %r: ignoring and continuing",
                user_id,
                room_id,
            )


DeactivateAccountHandler.deactivate_account = patched_deactivate_account
DeactivateAccountHandler._part_user = _patched_part_user
