"""Presence API.

Serves user presence for the whole homeserver as a paginated, long-polling
endpoint. Every Connect homeserver has exactly one universal shared room that
every user is a member of, which means Synapse's normal (shared-room
interest-filtered) presence machinery already covers every user — there is no
need for a bespoke "all users" cache or notification path. This module is a
thin re-exposure of the presence half of `/sync`:

  * `PresenceEventSource.get_new_events` supplies both the full snapshot
    (no `from_key`) and incremental deltas (`from_key` set).
  * `Notifier.wait_for_events` is the same long-poll primitive `/sync` uses,
    keyed on `StreamKeyType.PRESENCE`.

See `synapse.handlers.sync.SyncHandler.wait_for_sync_for_user` for the
canonical pattern this mirrors.
"""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, List, Optional, Tuple

import attr
from synapse.api.presence import UserPresenceState
from synapse.handlers.presence import format_user_presence_state
from synapse.http.server import JsonResource
from synapse.http.servlet import RestServlet, parse_integer, parse_string
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict, StreamToken

if TYPE_CHECKING:
    from synapse.server import HomeServer

# Default/maximum page size for both the initial snapshot and incremental
# deltas. Deltas are expected to almost always be far smaller than this (only
# users whose presence actually changed since `since`), but a burst (e.g. a
# reconnect storm) could exceed it, hence the overflow-paging path below.
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

# Default long-poll timeout when the client doesn't specify one, matching the
# spec's own default long-poll timeout used by `/sync`.
DEFAULT_TIMEOUT_MS = 30_000
MAX_TIMEOUT_MS = 60_000

_TOKEN_RE = re.compile(
    r"^(?P<phase>initial|live|live_page):(?P<rest>.*)$",
)


class PresenceTokenError(Exception):
    """Raised when a `since` token can't be parsed."""


@attr.s(auto_attribs=True, slots=True, frozen=True)
class InitialToken:
    """Mid initial-paging: `offset` into the pinned snapshot."""

    offset: int
    pinned_presence_key: int

    def encode(self) -> str:
        return f"initial:{self.offset}:{self.pinned_presence_key}"


@attr.s(auto_attribs=True, slots=True, frozen=True)
class LiveToken:
    """Caught up: incremental long-poll mode from `presence_key`."""

    presence_key: int

    def encode(self) -> str:
        return f"live:{self.presence_key}"


@attr.s(auto_attribs=True, slots=True, frozen=True)
class LivePageToken:
    """Mid overflow-paging of a delta larger than the page limit."""

    presence_key: int
    offset: int

    def encode(self) -> str:
        return f"live_page:{self.presence_key}:{self.offset}"


def _decode_since(since: str):
    match = _TOKEN_RE.match(since)
    if not match:
        raise PresenceTokenError(f"Malformed since token: {since!r}")
    phase = match.group("phase")
    rest = match.group("rest")
    try:
        if phase == "initial":
            offset_str, pinned_str = rest.split(":")
            return InitialToken(
                offset=int(offset_str), pinned_presence_key=int(pinned_str)
            )
        if phase == "live":
            return LiveToken(presence_key=int(rest))
        if phase == "live_page":
            key_str, offset_str = rest.split(":")
            return LivePageToken(presence_key=int(key_str), offset=int(offset_str))
    except ValueError as e:
        raise PresenceTokenError(f"Malformed since token: {since!r}") from e
    raise PresenceTokenError(f"Malformed since token: {since!r}")


class PresenceResource(JsonResource):
    """A resource exposing the paginated, long-polling presence endpoint."""

    def __init__(self, hs: "HomeServer"):
        JsonResource.__init__(self, hs, canonical_json=False, extract_context=True)
        PresenceServlet(hs).register(self)


class PresenceServlet(RestServlet):
    """`GET /_connect/presence` — cached, paginated, long-polling presence."""

    PATTERNS = [re.compile("^/_connect/presence$")]
    CATEGORY = "Presence requests"

    def __init__(self, hs: "HomeServer"):
        super().__init__()
        self.auth = hs.get_auth()
        self.clock = hs.get_clock()
        self.store = hs.get_datastores().main
        self.notifier = hs.get_notifier()
        self.presence_source = hs.get_event_sources().sources.presence

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        """Handle GET request to fetch presence for all users on the homeserver.

        Args:
            request:
                request from the connect client. Query params:
                limit - max number of users to return in this page (default/cap
                as above); since - opaque pagination/long-poll token from a
                previous response's next_batch, omitted on the very first
                request; timeout - long-poll timeout in ms, only consulted once
                the client has caught up to live mode, ignored during initial
                paging.

        Returns:
            A dict shaped like {"presence": [...], "next_batch": "<token>"}.
            Each entry is shaped like the native /presence/{user}/status
            response (presence, user_id, last_active_ago, status_msg,
            currently_active).
        """
        requester = await self.auth.get_user_by_req(request)
        user = requester.user
        limit = min(parse_integer(request, "limit", default=DEFAULT_LIMIT), MAX_LIMIT)
        if limit <= 0:
            limit = DEFAULT_LIMIT
        timeout = min(
            parse_integer(request, "timeout", default=DEFAULT_TIMEOUT_MS),
            MAX_TIMEOUT_MS,
        )
        since = parse_string(request, "since", default=None)

        if since is None:
            return HTTPStatus.OK, await self._initial_page(user, limit)

        try:
            token = _decode_since(since)
        except PresenceTokenError:
            # Stale/invalid token (or too old for the stream-change cache to
            # answer precisely) — ask the client to re-sync from scratch,
            # mirroring how a client re-initial-syncs on an unrecognised token.
            return HTTPStatus.OK, await self._initial_page(user, limit)

        if isinstance(token, InitialToken):
            return HTTPStatus.OK, await self._initial_page(
                user,
                limit,
                offset=token.offset,
                pinned_presence_key=token.pinned_presence_key,
            )
        if isinstance(token, LivePageToken):
            return HTTPStatus.OK, await self._live_page(user, limit, token)
        return HTTPStatus.OK, await self._live(user, limit, timeout, token)

    async def _snapshot(self, user) -> Tuple[List[UserPresenceState], int]:
        """The full current presence snapshot for every user, sorted by user_id.

        Args:
            user:
                the requesting user, passed through to the presence source.

        Returns:
            a tuple of (states, max_token) — the sorted presence states and
            the stream token representing the snapshot's upper bound.
        """
        states, max_token = await self.presence_source.get_new_events(
            user=user, from_key=None, include_offline=True
        )
        states = sorted(states, key=lambda s: s.user_id)
        return states, max_token

    async def _initial_page(
        self,
        user,
        limit: int,
        offset: int = 0,
        pinned_presence_key: Optional[int] = None,
    ) -> JsonDict:
        if pinned_presence_key is None:
            states, max_token = await self._snapshot(user)
        else:
            # Continuing a paged initial sync: re-fetch under the SAME pinned
            # token so the snapshot is stable across pages (the delta between
            # the pin and "now" is picked up by the first incremental request).
            states, _ = await self.presence_source.get_new_events(
                user=user, from_key=None, include_offline=True
            )
            states = sorted(states, key=lambda s: s.user_id)
            max_token = pinned_presence_key

        page = states[offset : offset + limit]
        next_offset = offset + len(page)
        if next_offset < len(states):
            next_batch = InitialToken(
                offset=next_offset, pinned_presence_key=max_token
            ).encode()
        else:
            next_batch = LiveToken(presence_key=max_token).encode()

        return self._response(page, next_batch)

    async def _live(self, user, limit: int, timeout: int, token: LiveToken) -> JsonDict:
        from_key = token.presence_key
        max_token = self.store.get_current_presence_token()

        if timeout == 0:
            states, new_key = await self.presence_source.get_new_events(
                user=user, from_key=from_key, include_offline=True
            )
            return self._live_result(states, new_key or max_token, limit)

        # Seed the wait with the real current full StreamToken (needed so
        # `Notifier.wait_for_events`'s listener registration/comparison is
        # correct) but override its presence component with our own
        # last-seen position. Non-presence components are irrelevant to us:
        # if they cause a spurious wake, our callback still only returns
        # truthy on an actual presence delta, so wait_for_events loops until
        # timeout or a real presence change (this mirrors how sync's own
        # long-poll behaves for any single stream type).
        current_full_token = self.notifier.event_sources.get_current_token()
        from_token = attr.evolve(current_full_token, presence_key=from_key)

        async def callback(before: StreamToken, after: StreamToken):
            states, new_key = await self.presence_source.get_new_events(
                user=user, from_key=before.presence_key, include_offline=True
            )
            if not states:
                return None
            return states, new_key

        result = await self.notifier.wait_for_events(
            user.to_string(), timeout, callback, from_token=from_token
        )
        if result is None:
            return self._live_result([], from_key, limit)
        states, new_key = result
        return self._live_result(states, new_key, limit)

    async def _live_page(self, user, limit: int, token: LivePageToken) -> JsonDict:
        # Overflow continuation: re-derive the same delta (the stream-change
        # cache is stable for a fixed `from_key` within its retention window)
        # and slice further into it. No blocking — the client is draining a
        # delta it's already been told is waiting.
        states, _ = await self.presence_source.get_new_events(
            user=user, from_key=token.presence_key, include_offline=True
        )
        return self._live_result(states, token.presence_key, limit, offset=token.offset)

    def _live_result(
        self,
        states: List[UserPresenceState],
        presence_key: int,
        limit: int,
        offset: int = 0,
    ) -> JsonDict:
        states = sorted(states, key=lambda s: s.user_id)
        page = states[offset : offset + limit]
        next_offset = offset + len(page)
        if next_offset < len(states):
            next_batch = LivePageToken(
                presence_key=presence_key, offset=next_offset
            ).encode()
        else:
            next_batch = LiveToken(presence_key=presence_key).encode()
        return self._response(page, next_batch)

    def _response(self, states: List[UserPresenceState], next_batch: str) -> JsonDict:
        now = self.clock.time_msec()
        return {
            "presence": [format_user_presence_state(s, now) for s in states],
            "next_batch": next_batch,
        }
