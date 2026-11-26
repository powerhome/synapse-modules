"""Tests for self-DM healing in connect.self_dm."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.api.constants import Membership

from connect.self_dm import SelfConversationCreator, UserHelpers

USER = "@u1:server"
PEER = "@u2:server"
LIVE = "!live:server"
DEAD = "!dead:server"
DEAD2 = "!dead2:server"
NEW = "!new:server"
PEER_DM = "!dm12:server"


class _NoopLinearizerCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


class _NoopLinearizer:
    """Stand-in for synapse Linearizer that avoids the twisted reactor in tests."""

    def queue(self, _key):
        return _NoopLinearizerCtx()


class SelfDmHealingTestSuite(unittest.IsolatedAsyncioTestCase):
    def make_creator(
        self,
        *,
        healing_enabled=True,
        m_direct=None,
        memberships=None,
        deactivated=False,
        new_room_id=NEW,
    ):
        creator = SelfConversationCreator.__new__(SelfConversationCreator)
        creator.api = MagicMock()
        creator.unbridged_room_event_type = "com.powerhrg.room.unbridged"
        creator.healing_enabled = healing_enabled
        creator.main_store = MagicMock()
        creator._heal_linearizer = _NoopLinearizer()

        # In-memory m.direct, mutated by put_global so assertions see writes.
        self.m_direct = (
            dict(m_direct) if m_direct else (None if m_direct is None else {})
        )

        async def _get_global(user_id, key):
            if key == "m.direct":
                return self.m_direct
            return None

        async def _put_global(user_id, key, content):
            if key == "m.direct":
                self.m_direct = content

        creator.api.account_data_manager.get_global = AsyncMock(side_effect=_get_global)
        creator.api.account_data_manager.put_global = AsyncMock(side_effect=_put_global)

        memberships = memberships or {}

        async def _membership(user_id, room_id):
            m = memberships.get(room_id)
            return (m, "$evt" if m else None)

        creator.main_store.get_local_current_membership_for_user_in_room = AsyncMock(
            side_effect=_membership
        )

        creator.api.get_profile_for_user = AsyncMock(
            return_value=MagicMock(display_name="Alice")
        )
        creator.api.create_room = AsyncMock(return_value=(new_room_id, None))
        creator.api.create_and_send_event_into_room = AsyncMock()

        # UserHelpers.is_user_deactivated is a classmethod hitting the store;
        # patch it for the test's lifetime.
        patcher = patch.object(
            UserHelpers,
            "is_user_deactivated",
            AsyncMock(return_value=deactivated),
        )
        self.addCleanup(patcher.stop)
        patcher.start()

        return creator

    async def _login(self, creator):
        await creator.on_user_login(USER, "oidc", "idp")

    # --- login: live self-DM is left alone -----------------------------------

    async def test_login_live_self_dm_no_create(self):
        creator = self.make_creator(
            m_direct={USER: [LIVE]}, memberships={LIVE: Membership.JOIN}
        )
        await self._login(creator)
        creator.api.create_room.assert_not_called()
        self.assertEqual(self.m_direct, {USER: [LIVE]})

    # --- login: dead self-DM heals -------------------------------------------

    async def test_login_dead_self_dm_heals(self):
        creator = self.make_creator(
            m_direct={USER: [DEAD]}, memberships={DEAD: Membership.LEAVE}
        )
        await self._login(creator)
        creator.api.create_room.assert_awaited_once()
        self.assertEqual(self.m_direct[USER], [NEW])

    async def test_heal_puts_new_id_first_and_drops_all_dead(self):
        creator = self.make_creator(
            m_direct={USER: [DEAD, DEAD2]},
            memberships={DEAD: Membership.LEAVE, DEAD2: Membership.LEAVE},
        )
        await self._login(creator)
        self.assertEqual(self.m_direct[USER], [NEW])
        self.assertEqual(self.m_direct[USER][0], NEW)

    async def test_purged_room_treated_as_dead(self):
        # No membership row at all -> None -> dead.
        creator = self.make_creator(m_direct={USER: [DEAD]}, memberships={})
        await self._login(creator)
        creator.api.create_room.assert_awaited_once()
        self.assertEqual(self.m_direct[USER], [NEW])

    async def test_heal_preserves_peer_keys(self):
        creator = self.make_creator(
            m_direct={USER: [DEAD], PEER: [PEER_DM]},
            memberships={DEAD: Membership.LEAVE},
        )
        await self._login(creator)
        self.assertEqual(self.m_direct[USER], [NEW])
        self.assertEqual(self.m_direct[PEER], [PEER_DM])

    # --- login: first-time creation ------------------------------------------

    async def test_login_no_self_key_creates(self):
        creator = self.make_creator(m_direct={PEER: [PEER_DM]}, memberships={})
        await self._login(creator)
        creator.api.create_room.assert_awaited_once()
        self.assertEqual(self.m_direct[USER], [NEW])
        self.assertEqual(self.m_direct[PEER], [PEER_DM])

    async def test_login_no_account_data_creates(self):
        creator = self.make_creator(m_direct=None, memberships={})
        await self._login(creator)
        creator.api.create_room.assert_awaited_once()
        self.assertEqual(self.m_direct, {USER: [NEW]})

    # --- login: mix with one alive returns the live one ----------------------

    async def test_mixed_list_returns_live_and_does_not_create(self):
        creator = self.make_creator(
            m_direct={USER: [DEAD, LIVE]},
            memberships={DEAD: Membership.LEAVE, LIVE: Membership.JOIN},
        )
        await self._login(creator)
        creator.api.create_room.assert_not_called()
        self.assertEqual(self.m_direct, {USER: [DEAD, LIVE]})

    # --- deactivated user is skipped -----------------------------------------

    async def test_deactivated_user_skipped(self):
        creator = self.make_creator(
            m_direct={USER: [DEAD]},
            memberships={DEAD: Membership.LEAVE},
            deactivated=True,
        )
        await self._login(creator)
        creator.api.create_room.assert_not_called()
        self.assertEqual(self.m_direct, {USER: [DEAD]})

    # --- flag off: behavior unchanged ----------------------------------------

    async def test_flag_off_does_not_heal_dead_room(self):
        # With healing off, a dead first id is still treated as present, so no
        # room is created (identical to today's get-or-create).
        creator = self.make_creator(
            healing_enabled=False,
            m_direct={USER: [DEAD]},
            memberships={DEAD: Membership.LEAVE},
        )
        await self._login(creator)
        creator.api.create_room.assert_not_called()
        self.assertEqual(self.m_direct, {USER: [DEAD]})

    async def test_flag_off_creates_when_missing(self):
        creator = self.make_creator(healing_enabled=False, m_direct=None)
        await self._login(creator)
        creator.api.create_room.assert_awaited_once()
        self.assertEqual(self.m_direct, {USER: [NEW]})

    # --- profile update -------------------------------------------------------

    async def test_profile_update_skips_dead_self_dm(self):
        creator = self.make_creator(
            m_direct={USER: [DEAD]}, memberships={DEAD: Membership.LEAVE}
        )
        await creator.on_profile_update(
            USER, MagicMock(display_name="Alice New"), False, False
        )
        creator.api.create_and_send_event_into_room.assert_not_called()

    async def test_profile_update_renames_live_self_dm(self):
        creator = self.make_creator(
            m_direct={USER: [LIVE]}, memberships={LIVE: Membership.JOIN}
        )
        await creator.on_profile_update(
            USER, MagicMock(display_name="Alice New"), False, False
        )
        creator.api.create_and_send_event_into_room.assert_awaited_once()
        event = creator.api.create_and_send_event_into_room.await_args.args[0]
        self.assertEqual(event["room_id"], LIVE)
        self.assertEqual(event["content"]["name"], "Alice New (You)")

    async def test_profile_update_flag_off_uses_first_id(self):
        # Unchanged behavior: with healing off, rename targets the first id
        # without a liveness check.
        creator = self.make_creator(
            healing_enabled=False,
            m_direct={USER: [DEAD]},
            memberships={DEAD: Membership.LEAVE},
        )
        await creator.on_profile_update(
            USER, MagicMock(display_name="Alice New"), False, False
        )
        creator.api.create_and_send_event_into_room.assert_awaited_once()
        event = creator.api.create_and_send_event_into_room.await_args.args[0]
        self.assertEqual(event["room_id"], DEAD)


if __name__ == "__main__":
    unittest.main()
