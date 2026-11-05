"""Tests for bulk user provisioning overlap coalescing and dedupe."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.http.site import SynapseRequest
from synapse.types import JsonDict
from twisted.internet import defer
from twisted.python.failure import Failure

from connect.populator.bulk_provisioning import BulkUserProvisioningServlet


class BulkProvisioningCoalesceTestSuite(unittest.IsolatedAsyncioTestCase):
    """Overlapping bulk requests coalesce to the latest snapshot without loss."""

    def setUp(self):
        self.hs = MagicMock()
        self.hs.get_auth = MagicMock()
        self.populator = MagicMock()
        self.populator.populate_all_users = AsyncMock()
        self.servlet = BulkUserProvisioningServlet(self.hs, self.populator)

        # Capture each scheduled run and hand back a Deferred we control so the
        # test can decide when (and how) each background populate completes.
        self.runs: list[tuple] = []

        def fake_run(name, func, *args):
            d = defer.Deferred()
            self.runs.append((d, func, args))
            return d

        self.servlet.api.run_as_background_process = MagicMock(side_effect=fake_run)

    @property
    def scheduled_payloads(self) -> list:
        """The `users` argument of every scheduled background populate.

        Returns:
            list: Each scheduled populate's user list.
        """
        return [args[0] for (_d, _func, args) in self.runs]

    def _complete(self, index: int, failure: Exception = None) -> None:
        """Fire the Deferred for the run at `index`, optionally as a failure.

        Args:
            index: Position in ``self.runs`` for the populate to complete.
            failure: When set, errback the Deferred with this exception.
        """
        d, _func, _args = self.runs[index]
        if failure is not None:
            d.errback(Failure(failure))
        else:
            d.callback(None)

    async def _post(self, users: list[JsonDict], version: int = None) -> tuple:
        request = MagicMock(spec=SynapseRequest)

        requester = MagicMock()
        requester.user.localpart = "audiences_bot"
        auth = self.hs.get_auth()
        auth.get_user_by_req = AsyncMock(return_value=requester)

        body = {"users": users}
        if version is not None:
            body["version"] = version

        with patch(
            "connect.populator.bulk_provisioning.parse_json_object_from_request",
            return_value=body,
        ):
            with patch("connect.populator.bulk_provisioning.init_request_context"):
                with patch("connect.populator.bulk_provisioning.log_event"):
                    return await self.servlet.on_POST(request)

    @staticmethod
    def _user(uid: str) -> JsonDict:
        return {"id": uid, "active": True}

    async def test_first_request_schedules_populate(self):
        status, _ = await self._post([self._user("a")])

        self.assertEqual(status, HTTPStatus.NO_CONTENT)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.scheduled_payloads[0], [self._user("a")])

    async def test_overlapping_request_runs_after_first_finishes(self):
        await self._post([self._user("a")])
        # Second, different snapshot arrives while the first run is in flight.
        await self._post([self._user("b")])

        # Nothing new scheduled yet; it is queued as pending.
        self.assertEqual(len(self.runs), 1)

        # First run finishes -> queued snapshot drains and runs.
        self._complete(0)

        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.scheduled_payloads[1], [self._user("b")])

    async def test_latest_overlapping_snapshot_replaces_pending(self):
        await self._post([self._user("a")])
        await self._post([self._user("b")])
        await self._post([self._user("c")])

        self.assertEqual(len(self.runs), 1)

        self._complete(0)

        # Only the newest queued snapshot (c) runs; b was superseded.
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.scheduled_payloads[1], [self._user("c")])

    async def test_request_matching_in_flight_clears_pending(self):
        await self._post([self._user("a")])
        await self._post([self._user("b")])  # queued as pending
        # A request equal to the in-flight snapshot supersedes the pending one.
        await self._post([self._user("a")])

        self._complete(0)

        # Pending was cleared, so nothing runs after the first.
        self.assertEqual(len(self.runs), 1)

    async def test_identical_request_after_success_is_noop(self):
        await self._post([self._user("a")])
        self._complete(0)

        status, _ = await self._post([self._user("a")])

        self.assertEqual(status, HTTPStatus.NO_CONTENT)
        self.assertEqual(len(self.runs), 1)

    async def test_failed_run_does_not_stick_in_dedupe_cache(self):
        await self._post([self._user("a")])
        self._complete(0, failure=RuntimeError("boom"))

        # An identical payload must re-run because the previous attempt failed.
        await self._post([self._user("a")])

        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.scheduled_payloads[1], [self._user("a")])

    async def test_failed_run_still_drains_pending(self):
        await self._post([self._user("a")])
        await self._post([self._user("b")])  # queued as pending
        self._complete(0, failure=RuntimeError("boom"))

        # Pending snapshot still runs even though the previous run failed.
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.scheduled_payloads[1], [self._user("b")])

    # --- version token: latest-computed snapshot wins ---------------------

    async def test_stale_snapshot_ignored_after_higher_version(self):
        await self._post([self._user("a")], version=20)
        self._complete(0)

        # A snapshot computed earlier (lower version) arrives late and is dropped.
        status, _ = await self._post([self._user("b")], version=10)

        self.assertEqual(status, HTTPStatus.NO_CONTENT)
        self.assertEqual(len(self.runs), 1)

    async def test_equal_version_ignored(self):
        await self._post([self._user("a")], version=10)
        self._complete(0)

        await self._post([self._user("b")], version=10)

        self.assertEqual(len(self.runs), 1)

    async def test_out_of_order_older_snapshot_not_queued(self):
        # Higher version is in flight; an older snapshot arriving during the run
        # must not be queued as pending.
        await self._post([self._user("a")], version=20)
        await self._post([self._user("b")], version=10)

        self._complete(0)

        self.assertEqual(len(self.runs), 1)

    async def test_higher_version_accepted_and_runs(self):
        await self._post([self._user("a")], version=10)
        self._complete(0)

        await self._post([self._user("b")], version=20)

        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.scheduled_payloads[1], [self._user("b")])
        self.assertEqual(self.servlet.latest_version, 20)

    async def test_pending_drains_with_latest_version(self):
        await self._post([self._user("a")], version=10)
        await self._post([self._user("b")], version=20)  # queued as pending

        self._complete(0)

        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.scheduled_payloads[1], [self._user("b")])
        self.assertEqual(self.servlet.latest_version, 20)

    async def test_missing_version_still_applies(self):
        # Older caller without a token keeps latest-arriving behaviour and does
        # not arm the staleness guard.
        await self._post([self._user("a")])
        self._complete(0)

        await self._post([self._user("b")])

        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.scheduled_payloads[1], [self._user("b")])
        self.assertIsNone(self.servlet.latest_version)


if __name__ == "__main__":
    unittest.main()
