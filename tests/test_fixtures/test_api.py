"""Tests for the deployment, credentials and teardown servlets."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.api.errors import SynapseError

from fixtures.api import (
    CleanupServlet,
    CredentialsServlet,
    DeploymentServlet,
    DeploymentsServlet,
    FixturesRootServlet,
)


def record(status="complete", **fields):
    return MagicMock(
        id="dep1",
        status=status,
        is_unfinished=status in ("queued", "running", "deprovisioning"),
        error=fields.get("error"),
        error_code=fields.get("error_code"),
        errcode=fields.get("errcode"),
    )


def make_runner(admitted=None, waited=None):
    runner = MagicMock()
    runner.admit = AsyncMock(return_value=admitted or record("queued"))
    runner.wait = AsyncMock(return_value=waited or record("complete"))
    runner.report = AsyncMock(return_value={"status": "complete", "users": {}})
    runner.get = AsyncMock(return_value=record())
    runner.delete = AsyncMock(return_value={"status": "deprovisioning"})
    runner.reissue_credentials = AsyncMock(return_value={"users": {}})
    runner.teardown = AsyncMock(return_value={"deactivated": 1, "rooms_purged": 1})
    return runner


async def post(servlet, body, wait=None):
    request = MagicMock()
    request.args = {b"wait": [str(wait).encode()]} if wait is not None else {}
    with patch("fixtures.api.parse_json_object_from_request", return_value=body):
        with patch("fixtures.api.init_request_context"):
            return await servlet.on_POST(request)


class DeploymentsServletTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_without_wait_answers_202_with_the_deployment_id(self):
        runner = make_runner()
        servlet = DeploymentsServlet(MagicMock(), runner)

        code, body = await post(servlet, {"run_id": "run-a"})

        self.assertEqual(code, HTTPStatus.ACCEPTED)
        self.assertEqual(body, {"deployment_id": "dep1", "status": "queued"})
        runner.wait.assert_not_awaited()

    async def test_passes_the_key_apart_from_the_scenario(self):
        runner = make_runner()
        servlet = DeploymentsServlet(MagicMock(), runner)

        await post(servlet, {"run_id": "run-a", "key": "k1"})

        runner.admit.assert_awaited_once_with({"run_id": "run-a"}, "k1")

    async def test_a_deployment_finished_within_the_wait_returns_its_graph(self):
        runner = make_runner()
        servlet = DeploymentsServlet(MagicMock(), runner)

        code, body = await post(servlet, {"run_id": "run-a"}, wait=30)

        self.assertEqual(code, HTTPStatus.OK)
        self.assertEqual(body["status"], "complete")
        runner.report.assert_awaited_once()
        self.assertTrue(runner.report.await_args.kwargs["collect"])

    async def test_a_deployment_still_running_after_the_wait_answers_202(self):
        runner = make_runner(waited=record("running"))
        servlet = DeploymentsServlet(MagicMock(), runner)

        code, body = await post(servlet, {"run_id": "run-a"}, wait=1)

        self.assertEqual(code, HTTPStatus.ACCEPTED)
        self.assertEqual(body["status"], "running")

    async def test_a_deployment_that_failed_within_the_wait_answers_with_its_error(
        self,
    ):
        runner = make_runner(
            waited=record(
                "failed",
                error="name taken",
                error_code=HTTPStatus.CONFLICT,
                errcode="M_USER_IN_USE",
            )
        )
        servlet = DeploymentsServlet(MagicMock(), runner)

        with self.assertRaises(SynapseError) as ctx:
            await post(servlet, {"run_id": "run-a"}, wait=30)

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
        self.assertEqual(ctx.exception.errcode, "M_USER_IN_USE")
        self.assertEqual(ctx.exception.msg, "name taken")

    async def test_rejects_an_unauthenticated_request(self):
        provisioner = MagicMock()
        provisioner.authenticate.side_effect = SynapseError(HTTPStatus.FORBIDDEN, "no")
        runner = make_runner()
        servlet = DeploymentsServlet(provisioner, runner)

        with self.assertRaises(SynapseError):
            await post(servlet, {"run_id": "run-a"})

        runner.admit.assert_not_awaited()


class DeploymentServletTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_reports_a_deployment_collecting_its_credentials(self):
        runner = make_runner()
        servlet = DeploymentServlet(MagicMock(), runner)

        with patch("fixtures.api.init_request_context"):
            code, body = await servlet.on_GET(MagicMock(), "dep1")

        self.assertEqual(code, HTTPStatus.OK)
        self.assertTrue(runner.report.await_args.kwargs["collect"])

    async def test_an_unknown_deployment_is_not_found(self):
        runner = make_runner()
        runner.get = AsyncMock(return_value=None)
        servlet = DeploymentServlet(MagicMock(), runner)

        with patch("fixtures.api.init_request_context"):
            with self.assertRaises(SynapseError) as ctx:
                await servlet.on_GET(MagicMock(), "nope")

        self.assertEqual(ctx.exception.code, HTTPStatus.NOT_FOUND)

    async def test_deleting_accepts_and_returns_the_status(self):
        runner = make_runner()
        servlet = DeploymentServlet(MagicMock(), runner)

        with patch("fixtures.api.init_request_context"):
            code, body = await servlet.on_DELETE(MagicMock(), "dep1")

        self.assertEqual(code, HTTPStatus.ACCEPTED)
        self.assertEqual(body["status"], "deprovisioning")


class CredentialsServletTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_reissues_credentials(self):
        runner = make_runner()
        servlet = CredentialsServlet(MagicMock(), runner)

        with patch("fixtures.api.init_request_context"):
            code, _ = await servlet.on_POST(MagicMock(), "dep1")

        self.assertEqual(code, HTTPStatus.OK)
        runner.reissue_credentials.assert_awaited_once_with("dep1")


class TeardownServletsTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_run_teardown_goes_through_the_runner(self):
        runner = make_runner()
        servlet = CleanupServlet(MagicMock(), runner)

        with patch("fixtures.api.init_request_context"):
            code, body = await servlet.on_DELETE(MagicMock(), "run-a")

        self.assertEqual(code, HTTPStatus.OK)
        runner.teardown.assert_awaited_once_with("run-a")
        self.assertEqual(body["run_id"], "run-a")

    async def test_global_teardown_goes_through_the_runner(self):
        runner = make_runner()
        servlet = FixturesRootServlet(MagicMock(), runner)

        with patch("fixtures.api.init_request_context"):
            await servlet.on_DELETE(MagicMock())

        runner.teardown.assert_awaited_once_with(None)


if __name__ == "__main__":
    unittest.main()
