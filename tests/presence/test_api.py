"""Tests for the presence servlet."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from synapse.api.presence import UserPresenceState
from synapse.types import StreamToken, UserID

from presence.api import (
    DEFAULT_LIMIT,
    InitialToken,
    LivePageToken,
    LiveToken,
    PresenceServlet,
    PresenceTokenError,
    _decode_since,
)


def make_state(user_id: str, state: str = "online") -> UserPresenceState:
    return UserPresenceState(
        user_id=user_id,
        state=state,
        last_active_ts=1000,
        last_federation_update_ts=1000,
        last_user_sync_ts=1000,
        status_msg=None,
        currently_active=(state == "online"),
    )


class TokenCodecTestSuite(unittest.TestCase):
    def test_initial_token_roundtrips(self):
        token = InitialToken(offset=5, pinned_presence_key=42)
        self.assertEqual(_decode_since(token.encode()), token)

    def test_live_token_roundtrips(self):
        token = LiveToken(presence_key=99)
        self.assertEqual(_decode_since(token.encode()), token)

    def test_live_page_token_roundtrips(self):
        token = LivePageToken(presence_key=99, offset=3)
        self.assertEqual(_decode_since(token.encode()), token)

    def test_malformed_token_raises(self):
        with self.assertRaises(PresenceTokenError):
            _decode_since("not-a-real-token")

    def test_unknown_phase_raises(self):
        with self.assertRaises(PresenceTokenError):
            _decode_since("bogus:1:2")


def make_servlet(get_new_events=None, current_presence_token: int = 0):
    hs = MagicMock()
    hs.get_auth.return_value = MagicMock()
    hs.get_clock.return_value.time_msec.return_value = 5_000
    hs.get_datastores.return_value.main.get_current_presence_token.return_value = (
        current_presence_token
    )
    hs.get_notifier.return_value = MagicMock()
    presence_source = MagicMock()
    if get_new_events is not None:
        presence_source.get_new_events = get_new_events
    hs.get_event_sources.return_value.sources.presence = presence_source

    servlet = PresenceServlet(hs)
    servlet.auth.get_user_by_req = AsyncMock(
        return_value=MagicMock(user=UserID.from_string("@requester:server"))
    )
    return servlet


class InitialPagingTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_first_page_returns_up_to_limit_and_an_initial_next_batch(self):
        states = [make_state(f"@u{i}:server") for i in range(5)]
        get_new_events = AsyncMock(return_value=(states, 10))
        servlet = make_servlet(get_new_events=get_new_events)

        result = await servlet._initial_page(
            UserID.from_string("@requester:server"), limit=3
        )

        self.assertEqual(len(result["presence"]), 3)
        self.assertEqual(
            [p["user_id"] for p in result["presence"]],
            ["@u0:server", "@u1:server", "@u2:server"],
        )
        self.assertEqual(
            result["next_batch"],
            InitialToken(offset=3, pinned_presence_key=10).encode(),
        )

    async def test_final_page_flips_to_live_token(self):
        states = [make_state(f"@u{i}:server") for i in range(2)]
        get_new_events = AsyncMock(return_value=(states, 10))
        servlet = make_servlet(get_new_events=get_new_events)

        result = await servlet._initial_page(
            UserID.from_string("@requester:server"), limit=5
        )

        self.assertEqual(len(result["presence"]), 2)
        self.assertEqual(result["next_batch"], LiveToken(presence_key=10).encode())

    async def test_continuation_page_uses_pinned_token_not_a_fresh_one(self):
        states = [make_state(f"@u{i}:server") for i in range(5)]
        get_new_events = AsyncMock(return_value=(states, 999))
        servlet = make_servlet(get_new_events=get_new_events)

        # Pinned token from the first page (10) must be preserved even though
        # the mock would otherwise report a newer one (999).
        result = await servlet._initial_page(
            UserID.from_string("@requester:server"),
            limit=3,
            offset=3,
            pinned_presence_key=10,
        )

        self.assertEqual(
            [p["user_id"] for p in result["presence"]], ["@u3:server", "@u4:server"]
        )
        self.assertEqual(result["next_batch"], LiveToken(presence_key=10).encode())

    async def test_pages_cover_the_whole_snapshot_with_no_gaps_or_dupes(self):
        states = [make_state(f"@u{i}:server") for i in range(7)]
        get_new_events = AsyncMock(return_value=(states, 10))
        servlet = make_servlet(get_new_events=get_new_events)
        user = UserID.from_string("@requester:server")

        seen = []
        page = await servlet._initial_page(user, limit=3)
        seen.extend(p["user_id"] for p in page["presence"])
        since = page["next_batch"]

        while since.startswith("initial:"):
            token = _decode_since(since)
            page = await servlet._initial_page(
                user,
                limit=3,
                offset=token.offset,
                pinned_presence_key=token.pinned_presence_key,
            )
            seen.extend(p["user_id"] for p in page["presence"])
            since = page["next_batch"]

        self.assertEqual(seen, [s.user_id for s in states])
        self.assertEqual(since, LiveToken(presence_key=10).encode())


class IncrementalTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_zero_returns_current_delta_immediately(self):
        states = [make_state("@changed:server")]
        get_new_events = AsyncMock(return_value=(states, 20))
        servlet = make_servlet(get_new_events=get_new_events, current_presence_token=20)
        servlet.notifier.wait_for_events = AsyncMock(
            side_effect=AssertionError("should not long-poll when timeout=0")
        )

        result = await servlet._live(
            UserID.from_string("@requester:server"),
            limit=DEFAULT_LIMIT,
            timeout=0,
            token=LiveToken(presence_key=10),
        )

        get_new_events.assert_awaited_once()
        self.assertEqual(get_new_events.await_args.kwargs["from_key"], 10)
        self.assertEqual(result["presence"][0]["user_id"], "@changed:server")
        self.assertEqual(result["next_batch"], LiveToken(presence_key=20).encode())

    async def test_long_poll_wakes_with_a_real_delta(self):
        states = [make_state("@changed:server")]
        servlet = make_servlet(current_presence_token=20)
        servlet.notifier.event_sources.get_current_token.return_value = (
            StreamToken.START
        )

        async def fake_wait_for_events(user_id, timeout, callback, from_token):
            before = from_token
            after = StreamToken.START
            return await callback(before, after)

        servlet.notifier.wait_for_events = AsyncMock(side_effect=fake_wait_for_events)
        servlet.presence_source.get_new_events = AsyncMock(return_value=(states, 20))

        result = await servlet._live(
            UserID.from_string("@requester:server"),
            limit=DEFAULT_LIMIT,
            timeout=30_000,
            token=LiveToken(presence_key=10),
        )

        self.assertEqual(result["presence"][0]["user_id"], "@changed:server")
        self.assertEqual(result["next_batch"], LiveToken(presence_key=20).encode())
        # The seeded from_token must carry OUR presence position, not START's.
        _, kwargs = servlet.notifier.wait_for_events.call_args
        self.assertEqual(kwargs["from_token"].presence_key, 10)

    async def test_long_poll_timeout_returns_empty_with_unchanged_token(self):
        servlet = make_servlet(current_presence_token=20)
        servlet.notifier.event_sources.get_current_token.return_value = (
            StreamToken.START
        )
        servlet.notifier.wait_for_events = AsyncMock(return_value=None)
        servlet.presence_source.get_new_events = AsyncMock()

        result = await servlet._live(
            UserID.from_string("@requester:server"),
            limit=DEFAULT_LIMIT,
            timeout=30_000,
            token=LiveToken(presence_key=10),
        )

        self.assertEqual(result["presence"], [])
        self.assertEqual(result["next_batch"], LiveToken(presence_key=10).encode())

    async def test_delta_larger_than_limit_pages_via_live_page_token(self):
        states = [make_state(f"@u{i}:server") for i in range(5)]
        servlet = make_servlet(current_presence_token=20)

        result = servlet._live_result(states, presence_key=20, limit=3)

        self.assertEqual(len(result["presence"]), 3)
        self.assertEqual(
            result["next_batch"], LivePageToken(presence_key=20, offset=3).encode()
        )

    async def test_live_page_drains_the_remainder_without_reblocking(self):
        states = [make_state(f"@u{i}:server") for i in range(5)]
        get_new_events = AsyncMock(return_value=(states, 20))
        servlet = make_servlet(get_new_events=get_new_events)
        servlet.notifier.wait_for_events = AsyncMock(
            side_effect=AssertionError("should not block on an overflow page")
        )

        result = await servlet._live_page(
            UserID.from_string("@requester:server"),
            limit=3,
            token=LivePageToken(presence_key=20, offset=3),
        )

        self.assertEqual(
            [p["user_id"] for p in result["presence"]], ["@u3:server", "@u4:server"]
        )
        self.assertEqual(result["next_batch"], LiveToken(presence_key=20).encode())


class OnGetRoutingTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_no_since_takes_the_initial_path(self):
        states = [make_state("@a:server")]
        get_new_events = AsyncMock(return_value=(states, 1))
        servlet = make_servlet(get_new_events=get_new_events)
        request = MagicMock()
        request.args = {}

        status, body = await servlet.on_GET(request)

        self.assertEqual(status, 200)
        self.assertEqual(body["next_batch"], LiveToken(presence_key=1).encode())

    async def test_malformed_since_falls_back_to_initial_sync(self):
        states = [make_state("@a:server")]
        get_new_events = AsyncMock(return_value=(states, 1))
        servlet = make_servlet(get_new_events=get_new_events)
        request = MagicMock()
        request.args = {b"since": [b"garbage"]}

        status, body = await servlet.on_GET(request)

        self.assertEqual(status, 200)
        self.assertEqual(body["next_batch"], LiveToken(presence_key=1).encode())

    async def test_unauthenticated_request_is_rejected_before_any_presence_lookup(self):
        get_new_events = AsyncMock()
        servlet = make_servlet(get_new_events=get_new_events)
        servlet.auth.get_user_by_req = AsyncMock(side_effect=Exception("no auth"))
        request = MagicMock()
        request.args = {}

        with self.assertRaisesRegex(Exception, "no auth"):
            await servlet.on_GET(request)

        get_new_events.assert_not_awaited()
