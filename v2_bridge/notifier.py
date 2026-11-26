"""Tells Connect v2 about v3 room changes without holding up the v3 request."""

import logging
from collections import OrderedDict
from http import HTTPStatus
from typing import Awaitable, Callable, NamedTuple, Optional, Tuple

from synapse.module_api import ModuleApi

from .client import BridgeClient, BridgeRefusedError

logger = logging.getLogger(__name__)


class ExponentialBackoff:
    """How often, and how long apart, a failed notification is tried again.

    Each wait doubles up to a ceiling. Notifications are delivered one at a
    time, so there is no crowd of retries to spread out with jitter.

    The defaults keep trying for about seven minutes (2 + 4 + 8 + 16 + 32 +
    6 x 60 seconds), long enough to ride out a restart of the bridge or a
    nitro-web deploy.
    """

    def __init__(
        self,
        attempts: int = 12,
        base_seconds: float = 2.0,
        max_seconds: float = 60.0,
    ):
        self.attempts = attempts
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds

    def delay_after(self, attempt: int) -> float:
        """How long to wait after a failed attempt.

        Args:
            attempt: The attempt that failed, counting from 1.

        Returns:
            The seconds to wait before the next attempt.
        """
        return min(self.max_seconds, self.base_seconds * 2 ** (attempt - 1))


class _Notification(NamedTuple):
    # Which setting of which room the notification is about, e.g. ("archive", room_id).
    key: Tuple[str, str]
    # What is being sent, for the logs.
    description: str
    # Sends the notification once.
    send: Callable[[], Awaitable[None]]


class BridgeNotifier:
    """Sends v3 room changes to Connect v2 in the background, in order.

    The v3 change is already stored when a notification is queued, so it never
    fails or slows the request that made the change.

    Notifications are delivered one at a time, in the order the changes were
    made, so an older state of a room can never land in Connect v2 after a
    newer one, and a burst of changes (auto-archival archives thousands of
    rooms in one run) reaches the bridge at the pace it can take. Each
    notification carries the room's full state for one setting, so only the
    latest one matters: a newer change to the same setting of the same room
    replaces one still waiting, and stops one being retried.

    Transient failures (the bridge answering 5xx, timing out or being
    unreachable) are retried with backoff. When the bridge refuses a change
    (4xx) or the retries run out, Connect v2 stays out of sync, so the failure
    is logged at ERROR, which Sentry captures through its logging integration.
    Two refusals are only warnings, because there is nothing to fix: 404 (the
    room is not bridged) and 410 (the bridge is turned off in Connect v2).
    """

    def __init__(
        self,
        api: ModuleApi,
        client: BridgeClient,
        backoff: Optional[ExponentialBackoff] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ):
        self.api = api
        self.client = client
        self.backoff = backoff or ExponentialBackoff()
        self.sleep = sleep or api.sleep
        self._pending: "OrderedDict[Tuple[str, str], _Notification]" = OrderedDict()
        self._delivering = False

    def archive_status_changed(
        self, room_id: str, archive: bool, initiator: str
    ) -> None:
        self._enqueue(
            _Notification(
                ("archive", room_id),
                f"{'archive' if archive else 'unarchive'} of {room_id}",
                lambda: self.client.update_archive_status(room_id, archive, initiator),
            )
        )

    def broadcasters_changed(
        self, room_id: str, broadcasters: list[str], initiator: str
    ) -> None:
        self._enqueue(
            _Notification(
                ("broadcasters", room_id),
                f"broadcasters {broadcasters} of {room_id}",
                lambda: self.client.update_broadcasters(
                    room_id, broadcasters, initiator
                ),
            )
        )

    def _enqueue(self, notification: _Notification) -> None:
        # Re-inserting moves a replaced notification to the back, behind every
        # change made before this one.
        self._pending.pop(notification.key, None)
        self._pending[notification.key] = notification

        if not self._delivering:
            self._delivering = True
            self.api.run_as_background_process(
                "notify_v2_bridge", self._deliver_pending
            )

    async def _deliver_pending(self) -> None:
        try:
            while self._pending:
                _, notification = self._pending.popitem(last=False)
                await self._deliver(notification)
        finally:
            self._delivering = False

    def _superseded(self, notification: _Notification) -> bool:
        return notification.key in self._pending

    async def _deliver(self, notification: _Notification) -> None:
        """Send one notification, retrying transient failures.

        Args:
            notification: The notification to send.
        """
        description = notification.description
        for attempt in range(1, self.backoff.attempts + 1):
            try:
                await notification.send()
                logger.info(f"Sent {description} to the Connect v2 bridge")
                return
            except BridgeRefusedError as e:
                self._log_refusal(description, e)
                return
            except Exception as e:
                if attempt == self.backoff.attempts:
                    logger.error(
                        f"Gave up sending {description} to the Connect v2 bridge after "
                        f"{attempt} attempts; Connect v2 is out of sync",
                        exc_info=e,
                    )
                    return

                delay = self.backoff.delay_after(attempt)
                logger.warning(
                    f"Attempt {attempt} to send {description} to the Connect v2 "
                    f"bridge failed ({e!r}); retrying in {delay:.1f}s"
                )
                await self.sleep(delay)

                if self._superseded(notification):
                    logger.info(
                        f"Not retrying {description}: a newer change to the room "
                        f"is queued for the Connect v2 bridge"
                    )
                    return

    @staticmethod
    def _log_refusal(description: str, refusal: BridgeRefusedError) -> None:
        if refusal.status == HTTPStatus.NOT_FOUND:
            # Not every v3 room has a Connect v2 counterpart.
            logger.warning(
                f"Not sending {description} to Connect v2: "
                f"the bridge does not know the room ({refusal})"
            )
        elif refusal.status == HTTPStatus.GONE:
            logger.warning(
                f"Not sending {description} to Connect v2: "
                f"the bridge is turned off there ({refusal})"
            )
        else:
            logger.error(
                f"Connect v2 bridge refused {description}; Connect v2 is out of sync",
                exc_info=refusal,
            )
