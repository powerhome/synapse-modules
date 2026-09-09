"""Tests for the unread counts API."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from unread_counts.api import UnreadCountsServlet

USER_ID = "@alice:powerhrg.com"
ROOM_A = "!a:powerhrg.com"
ROOM_B = "!b:powerhrg.com"


# Stands in for Synapse's `RoomNotifCounts` (main timeline + per-thread counts).
def notif_counts(main, threads=None):
    counts = MagicMock()
    counts.main_timeline.notify_count = main
    counts.threads = {
        thread_id: MagicMock(notify_count=count)
        for thread_id, count in (threads or {}).items()
    }
    return counts


# A servlet whose store answers BOTH of Synapse's unread-count paths.
#
# `counts` is the bulk `event_push_summary` read the servlet uses as a candidate
# filter; `confirmed` is the per-room re-count keyed by room id, defaulting to
# whatever the candidate said (i.e. the two paths agreeing). A test models the
# disagreement — the phantom invite notification — by passing a lower `confirmed`.
def make_servlet(counts, joined_room_ids, confirmed=None):
    confirmed = counts if confirmed is None else confirmed

    hs = MagicMock()
    store = MagicMock()
    store.get_unread_counts_by_room_for_user = AsyncMock(return_value=counts)
    store.get_rooms_for_user = AsyncMock(return_value=joined_room_ids)

    async def per_room(room_id, _user_id):
        return notif_counts(confirmed.get(room_id, 0))

    store.get_unread_event_push_actions_by_room_for_user = AsyncMock(
        side_effect=per_room
    )
    hs.get_datastores.return_value.main = store

    auth = MagicMock()
    requester = MagicMock()
    requester.user.to_string.return_value = USER_ID
    auth.get_user_by_req = AsyncMock(return_value=requester)
    hs.get_auth.return_value = auth

    servlet = UnreadCountsServlet(hs)
    return servlet, store


class UnreadCountsServletTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_joined_rooms_counts_as_a_flat_map(self):
        servlet, store = make_servlet({ROOM_A: 58, ROOM_B: 1}, {ROOM_A, ROOM_B})

        status, body = await servlet.on_GET(MagicMock())

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {ROOM_A: 58, ROOM_B: 1})
        store.get_unread_counts_by_room_for_user.assert_awaited_once_with(USER_ID)

    async def test_drops_a_room_the_user_has_left(self):
        # `event_push_summary` rows are not removed on leave, per
        # `get_unread_counts_by_room_for_user`'s own docstring — the servlet must
        # filter them out itself, or an ex-member would see a stale badge for a room
        # they can no longer even open.
        servlet, _store = make_servlet({ROOM_A: 58, ROOM_B: 3}, {ROOM_A})

        _status, body = await servlet.on_GET(MagicMock())

        self.assertEqual(body, {ROOM_A: 58})
        self.assertNotIn(ROOM_B, body)

    async def test_drops_zero_counts(self):
        servlet, _store = make_servlet({ROOM_A: 0, ROOM_B: 3}, {ROOM_A, ROOM_B})

        _status, body = await servlet.on_GET(MagicMock())

        self.assertEqual(body, {ROOM_B: 3})

    async def test_returns_an_empty_map_for_an_account_with_no_unread(self):
        servlet, store = make_servlet({}, {ROOM_A, ROOM_B})

        status, body = await servlet.on_GET(MagicMock())

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {})
        # Nothing to intersect against membership for, so the membership lookup is
        # skipped entirely.
        store.get_rooms_for_user.assert_not_awaited()

    async def test_authenticates_and_scopes_to_the_requesting_user_only(self):
        servlet, store = make_servlet({ROOM_A: 5}, {ROOM_A})
        request = MagicMock()

        await servlet.on_GET(request)

        servlet.auth.get_user_by_req.assert_awaited_once_with(request)
        store.get_unread_counts_by_room_for_user.assert_awaited_once_with(USER_ID)
        store.get_rooms_for_user.assert_awaited_once_with(USER_ID)

    async def test_drops_a_phantom_count_the_per_room_path_disowns(self):
        # The reported bug. Synapse's default `.m.rule.invite_for_me` notifies on the
        # `m.room.member` invite; `synapse_auto_accept_invite` then joins the user, so
        # the notification is never actionable and — with no read receipt, in a room
        # that may hold no readable event at all — nothing ever clears it. The bulk
        # summary read still reports it; the per-room path floors at the user's own
        # join and reports 0. The floored answer is the one clients get.
        servlet, store = make_servlet(
            {ROOM_A: 1, ROOM_B: 58},
            {ROOM_A, ROOM_B},
            confirmed={ROOM_A: 0, ROOM_B: 58},
        )

        _status, body = await servlet.on_GET(MagicMock())

        self.assertEqual(body, {ROOM_B: 58})
        self.assertNotIn(
            ROOM_A,
            body,
            "a pre-join invite notification must not reach the client as a badge",
        )
        store.get_unread_event_push_actions_by_room_for_user.assert_awaited_with(
            ROOM_B, USER_ID
        )

    async def test_prefers_the_per_room_count_when_the_summary_is_stale(self):
        # `event_push_summary` is rotated by a 30s background loop, so it can read
        # stale-high for a room just read on another device. The per-room path sees
        # the receipt immediately, and its number wins.
        servlet, _store = make_servlet({ROOM_A: 58}, {ROOM_A}, confirmed={ROOM_A: 3})

        _status, body = await servlet.on_GET(MagicMock())

        self.assertEqual(body, {ROOM_A: 3})

    async def test_never_re_counts_a_room_the_summary_calls_read(self):
        # The bulk read stays the candidate filter: a room it reports nothing for is
        # not re-counted, so the added cost is one query per *already-unread* room
        # rather than per joined room. This is what keeps the endpoint O(unread) on a
        # 250-room account.
        servlet, store = make_servlet({ROOM_A: 4}, {ROOM_A, ROOM_B})

        _status, body = await servlet.on_GET(MagicMock())

        self.assertEqual(body, {ROOM_A: 4})
        store.get_unread_event_push_actions_by_room_for_user.assert_awaited_once_with(
            ROOM_A, USER_ID
        )

    async def test_never_re_counts_a_room_the_user_has_left(self):
        servlet, store = make_servlet({ROOM_A: 4, ROOM_B: 9}, {ROOM_A})

        _status, body = await servlet.on_GET(MagicMock())

        self.assertEqual(body, {ROOM_A: 4})
        store.get_unread_event_push_actions_by_room_for_user.assert_awaited_once_with(
            ROOM_A, USER_ID
        )

    async def test_sums_thread_notifications_into_the_room_total(self):
        # Matches the bulk query ("threads are currently aggregated under their room")
        # and how `/sync` reports `notification_count` when it is not breaking threads
        # out separately, so switching sources cannot change a threaded room's badge.
        servlet, store = make_servlet({ROOM_A: 5}, {ROOM_A})
        store.get_unread_event_push_actions_by_room_for_user = AsyncMock(
            return_value=notif_counts(2, {"$thread1": 3, "$thread2": 4})
        )

        _status, body = await servlet.on_GET(MagicMock())

        self.assertEqual(body, {ROOM_A: 9})
