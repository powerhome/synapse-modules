"""Unread Counts API."""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.http.server import JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict

if TYPE_CHECKING:
    from synapse.server import HomeServer


class UnreadCountsResource(JsonResource):
    """A resource for reading per-room unread notification counts."""

    def __init__(self, hs: "HomeServer"):
        JsonResource.__init__(self, hs, canonical_json=False, extract_context=True)
        UnreadCountsServlet(hs).register(self)


class UnreadCountsServlet(RestServlet):
    """A servlet for reading the requester's per-room unread notification counts."""

    PATTERNS = [re.compile("^/_connect/unread_counts$")]
    CATEGORY = "Unread count requests"

    def __init__(self, hs: "HomeServer"):
        super().__init__()
        self.store = hs.get_datastores().main
        self.auth = hs.get_auth()

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        """Handle GET request for the requester's unread notification count per room.

        Clients on Simplified Sliding Sync get no server-side notification counts at
        all — `get_room_sync_data` hardcodes them to zero — so a client-side count is
        bounded by the sliding-sync `timeline_limit` until back-pagination reaches the
        read receipt. This endpoint hands them the server's own number instead.

        It exists because the two endpoints that already expose this number are both
        unusable here:

        - `GET /_matrix/client/v3/notifications` reads `event_push_actions`, which
          Synapse prunes of non-highlight rows after ~24h. Connect's 2-person DMs get
          no Connect push preference at all and `EVERY_MESSAGE` rooms install only a
          `suppress.edits` rule, so both notify through Synapse's built-in defaults
          and produce exactly the non-highlighted rows that get pruned.
        - An initial (`since`-less) `GET /_matrix/client/v3/sync` reports the right
          number but resolves full current state for every joined room to do it
          (`_compute_state_delta_for_full_sync`, `StateFilter.all()` with
          `await_full_state=True`, since Connect does not set `lazy_load_members`).
          Measured at 15+ seconds for a real production account.

        `get_unread_counts_by_room_for_user` is what Synapse's own push badges use
        (see `synapse/push/push_tools.py`, and `connect/receipts/badge.py` in this
        repo). It is three queries in one transaction, all covered by
        `event_push_summary_unique_index2 (user_id, room_id, thread_id)` and
        `event_push_actions_u_highlight (user_id, stream_ordering)`.

        It is, however, only a **candidate filter** here, not the answer — see
        `_confirmed_count`. Synapse has two unread-count paths and they disagree on
        exactly the rooms Connect creates most of.

        Known limitation: `event_push_summary` is filled by the `_rotate_notifs`
        background job, a 30s loop, so notifications from the last few seconds may not
        be counted yet. Callers are expected to combine this with their own live count
        — which covers exactly that recent tail — rather than treat it as the whole
        truth.

        Args:
            request: The HTTP request object.

        Returns:
            A tuple of the HTTP status code and a flat `{room_id: notification_count}`
            map for the requesting user. Rooms with no unread notifications are absent
            rather than present with a zero, so an account with nothing unread gets an
            empty object.
        """
        requester = await self.auth.get_user_by_req(request)
        user_id = requester.user.to_string()

        candidates = await self.store.get_unread_counts_by_room_for_user(user_id)
        if not candidates:
            return HTTPStatus.OK, {}

        # `event_push_summary` rows survive the user leaving the room, so the counts
        # have to be intersected with current membership — Synapse's own docstring on
        # `get_unread_counts_by_room_for_user` puts that filtering on the caller.
        joined_room_ids = await self.store.get_rooms_for_user(user_id)

        confirmed = {}
        for room_id, candidate in candidates.items():
            if candidate <= 0 or room_id not in joined_room_ids:
                continue
            count = await self._confirmed_count(room_id, user_id)
            if count > 0:
                confirmed[room_id] = count
        return HTTPStatus.OK, confirmed

    async def _confirmed_count(self, room_id: str, user_id: str) -> int:
        """Re-count `room_id` the way Synapse's own per-room path does.

        The bulk `get_unread_counts_by_room_for_user` floors each room's count at the
        user's **read receipts** and nothing else. The per-room
        `get_unread_event_push_actions_by_room_for_user` — the one `/sync` serves
        `unread_notifications` from — falls back to something more when there is no
        receipt (`_get_unread_counts_by_receipt_txn`, `event_push_actions.py`):

            If the user has no receipts in the room, retrieve the stream ordering for
            the latest membership event from this user in this room (which we assume
            is a join).

        That membership floor is load-bearing for Connect. Synapse's default
        `.m.rule.invite_for_me` notifies on the `m.room.member` **invite**, and
        `synapse_auto_accept_invite` then joins the user immediately — so the
        notification is never actionable, and nothing ever clears it, because clearing
        requires a read receipt and an audience/bridge-created room may hold no
        readable event at all. Without the floor every such room reports a permanent
        phantom `1`. Measured on a real 252-room account: 69 rooms reported unread, of
        which **68 contained zero `m.room.message` events** — the entire count was
        pre-join invites. `/sync` reported 0 for those same rooms.

        So the bulk query is used only to find the rooms worth asking about (rooms with
        nothing unread never appear), and each candidate is then confirmed here.

        **This makes the endpoint N+1, and the N is sequential.** Each confirmation is
        its own transaction of ~6 queries (receipt lookup, the membership fallback's
        `local_current_membership` + stream-ordering reads, the summary and highlight
        counts, and the rotation watermark), and the loop above awaits them one at a
        time. Measured against a real 252-room account with 69 unread candidates: 69
        rooms x 0.279 ms = 19.3 ms of SQL, so ~40-90 ms end-to-end once per-transaction
        and reactor overhead are counted. That is affordable, but it is 70 transactions
        rather than one, and latency grows linearly with the caller's *unread-room*
        count -- the number to watch against the client's 2 s request timeout, not the
        joined-room count that `unread_count_scale.rs` exercises.

        `get_unread_event_push_actions_by_room_for_user` is `@cached`, but
        `max_entries=5000` is shared across every user on the worker, so a login herd
        should be assumed to miss. If this ever needs to get cheaper, the fix is to
        express the membership floor as one bulk query rather than to parallelise the
        fan-out: `yieldable_gather_results` would cut latency without reducing the work,
        and would make each request burstier against a 10-connection-per-worker pool.

        Threads are summed into the room's total, matching both the bulk query
        ("threads are currently aggregated under their room") and how `/sync` reports
        `notification_count` when it is not breaking threads out separately
        (`handlers/sync.py`).

        Args:
            room_id: The room to re-count.
            user_id: The user to re-count it for.

        Returns:
            The room's confirmed notification count, threads included.
        """
        counts = await self.store.get_unread_event_push_actions_by_room_for_user(
            room_id, user_id
        )
        return counts.main_timeline.notify_count + sum(
            thread.notify_count for thread in counts.threads.values()
        )
