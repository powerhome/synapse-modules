"""Tests for sending v3 room changes to the Connect v2 bridge in the background."""

import unittest
from unittest.mock import AsyncMock, MagicMock, call

from connect.v2_bridge import (
    BridgeNotifier,
    BridgeRefusedError,
    BridgeUnavailableError,
    ExponentialBackoff,
)

ROOM_ID = "!room:server"
OTHER_ROOM_ID = "!other:server"
ALICE = "@alice:server"
NOTIFIER_LOGGER = "connect.v2_bridge.notifier"


class BackgroundProcesses:
    """Stands in for ModuleApi: holds background processes until the test runs them."""

    def __init__(self):
        self.names = []
        self.pending = []

    def run_as_background_process(self, name, func, *args):
        self.names.append(name)
        self.pending.append(func(*args))

    async def run_all(self):
        while self.pending:
            await self.pending.pop(0)


def make_client(**side_effects):
    client = MagicMock()
    client.update_archive_status = AsyncMock(side_effect=side_effects.get("archive"))
    client.update_broadcasters = AsyncMock(side_effect=side_effects.get("broadcasters"))
    return client


def five_attempts():
    return ExponentialBackoff(attempts=5, base_seconds=2.0, max_seconds=60.0)


class BridgeNotifierTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_sends_an_archive_change_in_the_background(self):
        api = BackgroundProcesses()
        client = make_client()
        notifier = BridgeNotifier(api, client, five_attempts(), AsyncMock())

        notifier.archive_status_changed(ROOM_ID, True, ALICE)

        client.update_archive_status.assert_not_called()
        await api.run_all()
        client.update_archive_status.assert_awaited_once_with(ROOM_ID, True, ALICE)
        self.assertEqual(api.names, ["notify_v2_bridge"])

    async def test_sends_a_broadcasters_change_in_the_background(self):
        api = BackgroundProcesses()
        client = make_client()
        notifier = BridgeNotifier(api, client, five_attempts(), AsyncMock())

        notifier.broadcasters_changed(ROOM_ID, [ALICE], ALICE)

        await api.run_all()
        client.update_broadcasters.assert_awaited_once_with(ROOM_ID, [ALICE], ALICE)

    async def test_sends_changes_one_at_a_time_in_the_order_they_were_made(self):
        api = BackgroundProcesses()
        client = MagicMock()
        client.update_archive_status = AsyncMock()
        client.update_broadcasters = client.update_archive_status
        notifier = BridgeNotifier(api, client, five_attempts(), AsyncMock())

        notifier.archive_status_changed(ROOM_ID, True, ALICE)
        notifier.broadcasters_changed(OTHER_ROOM_ID, [ALICE], ALICE)
        notifier.archive_status_changed(OTHER_ROOM_ID, True, ALICE)
        await api.run_all()

        self.assertEqual(
            client.update_archive_status.await_args_list,
            [
                call(ROOM_ID, True, ALICE),
                call(OTHER_ROOM_ID, [ALICE], ALICE),
                call(OTHER_ROOM_ID, True, ALICE),
            ],
        )
        self.assertEqual(api.names, ["notify_v2_bridge"])

    async def test_starts_again_for_changes_made_after_the_queue_emptied(self):
        api = BackgroundProcesses()
        client = make_client()
        notifier = BridgeNotifier(api, client, five_attempts(), AsyncMock())
        notifier.archive_status_changed(ROOM_ID, True, ALICE)
        await api.run_all()

        notifier.archive_status_changed(ROOM_ID, False, ALICE)
        await api.run_all()

        self.assertEqual(
            client.update_archive_status.await_args_list,
            [call(ROOM_ID, True, ALICE), call(ROOM_ID, False, ALICE)],
        )
        self.assertEqual(api.names, ["notify_v2_bridge", "notify_v2_bridge"])

    async def test_a_newer_change_replaces_one_still_waiting(self):
        api = BackgroundProcesses()
        client = make_client()
        notifier = BridgeNotifier(api, client, five_attempts(), AsyncMock())

        notifier.archive_status_changed(ROOM_ID, True, ALICE)
        notifier.archive_status_changed(ROOM_ID, False, ALICE)
        await api.run_all()

        client.update_archive_status.assert_awaited_once_with(ROOM_ID, False, ALICE)

    async def test_a_newer_change_stops_the_retries_of_an_older_one(self):
        api = BackgroundProcesses()
        client = make_client(archive=[BridgeUnavailableError("502"), None])

        async def unarchive_while_waiting(_delay):
            notifier.archive_status_changed(ROOM_ID, False, ALICE)

        notifier = BridgeNotifier(api, client, five_attempts(), unarchive_while_waiting)

        notifier.archive_status_changed(ROOM_ID, True, ALICE)
        await api.run_all()

        self.assertEqual(
            client.update_archive_status.await_args_list,
            [call(ROOM_ID, True, ALICE), call(ROOM_ID, False, ALICE)],
        )

    async def test_retries_transient_failures_with_growing_waits(self):
        api = BackgroundProcesses()
        client = make_client(
            broadcasters=[
                BridgeUnavailableError("bridge answered 502"),
                TimeoutError(),
                ConnectionRefusedError(),
                None,
            ]
        )
        sleep = AsyncMock()
        notifier = BridgeNotifier(api, client, five_attempts(), sleep)

        notifier.broadcasters_changed(ROOM_ID, [ALICE], ALICE)
        with self.assertNoLogs(NOTIFIER_LOGGER, level="ERROR"):
            await api.run_all()

        self.assertEqual(client.update_broadcasters.await_count, 4)
        self.assertEqual(sleep.await_args_list, [call(2.0), call(4.0), call(8.0)])

    async def test_reports_to_sentry_when_the_retries_run_out(self):
        api = BackgroundProcesses()
        client = make_client(archive=BridgeUnavailableError("bridge answered 502"))
        sleep = AsyncMock()
        notifier = BridgeNotifier(api, client, five_attempts(), sleep)

        notifier.archive_status_changed(ROOM_ID, True, ALICE)
        with self.assertLogs(NOTIFIER_LOGGER, level="ERROR") as logs:
            await api.run_all()

        self.assertEqual(client.update_archive_status.await_count, 5)
        self.assertEqual(sleep.await_count, 4)
        [record] = [r for r in logs.records if r.levelname == "ERROR"]
        self.assertIn(f"archive of {ROOM_ID}", record.getMessage())
        self.assertIsInstance(record.exc_info[1], BridgeUnavailableError)

    async def test_keeps_delivering_the_queue_after_a_failure(self):
        api = BackgroundProcesses()
        client = make_client(archive=BridgeRefusedError(422, "no v2 room"))
        notifier = BridgeNotifier(api, client, five_attempts(), AsyncMock())

        notifier.archive_status_changed(ROOM_ID, True, ALICE)
        notifier.broadcasters_changed(OTHER_ROOM_ID, [ALICE], ALICE)
        with self.assertLogs(NOTIFIER_LOGGER, level="ERROR"):
            await api.run_all()

        client.update_broadcasters.assert_awaited_once_with(
            OTHER_ROOM_ID, [ALICE], ALICE
        )

    async def test_reports_a_refusal_to_sentry_without_retrying(self):
        api = BackgroundProcesses()
        client = make_client(broadcasters=BridgeRefusedError(422, "no v2 user"))
        sleep = AsyncMock()
        notifier = BridgeNotifier(api, client, five_attempts(), sleep)

        notifier.broadcasters_changed(ROOM_ID, [ALICE], ALICE)
        with self.assertLogs(NOTIFIER_LOGGER, level="ERROR") as logs:
            await api.run_all()

        client.update_broadcasters.assert_awaited_once()
        sleep.assert_not_called()
        self.assertIsInstance(logs.records[0].exc_info[1], BridgeRefusedError)

    async def test_an_unbridged_room_is_only_a_warning(self):
        api = BackgroundProcesses()
        client = make_client(archive=BridgeRefusedError(404, "Room not found"))
        notifier = BridgeNotifier(api, client, five_attempts(), AsyncMock())

        notifier.archive_status_changed(ROOM_ID, False, ALICE)
        with self.assertNoLogs(NOTIFIER_LOGGER, level="ERROR"):
            await api.run_all()

        client.update_archive_status.assert_awaited_once()

    async def test_a_bridge_turned_off_in_v2_is_only_a_warning(self):
        api = BackgroundProcesses()
        client = make_client(archive=BridgeRefusedError(410, "Gone"))
        notifier = BridgeNotifier(api, client, five_attempts(), AsyncMock())

        notifier.archive_status_changed(ROOM_ID, True, ALICE)
        with self.assertNoLogs(NOTIFIER_LOGGER, level="ERROR"):
            await api.run_all()

        client.update_archive_status.assert_awaited_once()

    async def test_sleeps_through_the_module_api_by_default(self):
        api = MagicMock()

        notifier = BridgeNotifier(api, make_client())

        self.assertIs(notifier.sleep, api.sleep)


class ExponentialBackoffTestSuite(unittest.TestCase):
    def test_doubles_the_wait_up_to_the_maximum(self):
        backoff = ExponentialBackoff(base_seconds=2.0, max_seconds=10.0)

        delays = [backoff.delay_after(attempt) for attempt in range(1, 5)]

        self.assertEqual(delays, [2.0, 4.0, 8.0, 10.0])

    def test_keeps_trying_for_minutes_by_default(self):
        backoff = ExponentialBackoff()

        total = sum(
            backoff.delay_after(attempt) for attempt in range(1, backoff.attempts)
        )

        self.assertEqual(total, 422.0)


if __name__ == "__main__":
    unittest.main()
