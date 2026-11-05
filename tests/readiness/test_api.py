"""Unit tests for readiness API resource behavior."""

import unittest
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from synapse.http.server import JsonResource
from synapse.http.servlet import RestServlet

import connect.readiness.api as readiness_api
from connect.readiness.api import ReadinessResource, ReadinessServlet


def _fake_hs_with_db_pool(db_pool):
    """Build a fake homeserver exposing ``hs.get_datastores().main.db_pool``.

    Args:
        db_pool: Database pool object exposed by the fake homeserver.

    Returns:
        A fake homeserver object with ``get_datastores().main.db_pool`` configured.
    """
    main_store = SimpleNamespace(db_pool=db_pool)
    datastores = SimpleNamespace(main=main_store)
    hs = MagicMock()
    hs.get_datastores.return_value = datastores
    return hs


class ReadinessServletTestSuite(unittest.IsolatedAsyncioTestCase):
    def test_types_use_jsonresource_and_restservlet(self):
        self.assertTrue(issubclass(ReadinessResource, JsonResource))
        self.assertTrue(issubclass(ReadinessServlet, RestServlet))

    def test_resource_registers_servlets_on_init(self):
        hs = _fake_hs_with_db_pool(MagicMock())

        with patch.object(ReadinessResource, "register_servlets") as mock_register:
            resource = ReadinessResource(hs)

        mock_register.assert_called_once_with(resource, hs)

    def test_register_servlets_registers_readiness_servlet(self):
        hs = _fake_hs_with_db_pool(MagicMock())
        resource = MagicMock()

        with patch.object(ReadinessServlet, "register") as mock_register:
            ReadinessResource.register_servlets(resource, hs)

        mock_register.assert_called_once_with(resource)

    async def test_build_readiness_payload_reports_ready_when_db_query_succeeds(self):
        db_pool = MagicMock()
        db_pool.runInteraction = AsyncMock(return_value={"ok": True})
        hs = _fake_hs_with_db_pool(db_pool)

        servlet = ReadinessServlet(hs)
        status, payload = await servlet._build_readiness_payload()

        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["checks"]["database"], "ok")

        db_pool.runInteraction.assert_awaited_once()
        args, kwargs = db_pool.runInteraction.await_args
        self.assertEqual(args[0], "readiness_db_check")
        self.assertEqual(args[1], servlet._db_check_txn)
        self.assertTrue(kwargs["db_autocommit"])

    async def test_build_readiness_payload_reports_not_ready_when_db_query_fails(self):
        db_pool = MagicMock()
        db_pool.runInteraction = AsyncMock(side_effect=Exception("db down"))
        hs = _fake_hs_with_db_pool(db_pool)

        servlet = ReadinessServlet(hs)
        status, payload = await servlet._build_readiness_payload()

        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["checks"]["database"], "failed")
        self.assertEqual(payload["error"], "database query failed")
        db_pool.runInteraction.assert_awaited_once()

    async def test_on_get_returns_tuple_from_build_payload(self):
        hs = _fake_hs_with_db_pool(MagicMock())
        servlet = ReadinessServlet(hs)

        expected_payload = {"ready": True, "checks": {"database": "ok"}}
        with patch.object(
            servlet,
            "_build_readiness_payload",
            AsyncMock(return_value=(HTTPStatus.OK, expected_payload)),
        ) as mock_build:
            status, payload = await servlet.on_GET(MagicMock())

        mock_build.assert_awaited_once()
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload, expected_payload)

    def test_db_check_txn_returns_ok_on_expected_row(self):
        txn = MagicMock()
        txn.fetchone.return_value = (1,)

        result = ReadinessServlet._db_check_txn(txn)

        txn.execute.assert_called_once_with("SELECT 1")
        txn.fetchone.assert_called_once()
        self.assertEqual(result, {"ok": True})

    def test_db_check_txn_raises_on_unexpected_row(self):
        txn = MagicMock()
        txn.fetchone.return_value = (0,)

        with self.assertRaises(RuntimeError):
            ReadinessServlet._db_check_txn(txn)

        txn.execute.assert_called_once_with("SELECT 1")
        txn.fetchone.assert_called_once()

    def test_servlet_defines_get_handler_only(self):
        self.assertTrue(hasattr(ReadinessServlet, "on_GET"))
        self.assertFalse(hasattr(ReadinessServlet, "on_POST"))
        self.assertFalse(hasattr(ReadinessServlet, "on_PUT"))
        self.assertFalse(hasattr(ReadinessServlet, "on_DELETE"))

    def test_servlet_has_patterns(self):
        self.assertTrue(hasattr(ReadinessServlet, "PATTERNS"))
        self.assertGreaterEqual(len(ReadinessServlet.PATTERNS), 1)


class LegacyRenderBehaviorTestSuite(unittest.TestCase):
    """Guardrail test to ensure old Twisted render flow is gone."""

    def test_not_done_yet_path_is_not_used(self):
        self.assertNotIn("render", ReadinessResource.__dict__)
        self.assertFalse(hasattr(readiness_api, "NOT_DONE_YET"))


if __name__ == "__main__":
    unittest.main()
