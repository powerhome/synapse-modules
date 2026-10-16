"""Readiness HTTP resource for Synapse.

This resource exposes a lightweight endpoint intended for Kubernetes readiness
checks. It performs a database round-trip (`SELECT 1`) using Synapse's primary
database pool and returns JSON indicating readiness.
"""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Dict, Tuple

from synapse.http.server import HttpServer, JsonResource
from synapse.http.servlet import RestServlet
from synapse.http.site import SynapseRequest
from synapse.types import JsonDict

if TYPE_CHECKING:
    from synapse.server import HomeServer


class ReadinessResource(JsonResource):
    """JSON resource that registers readiness servlets."""

    def __init__(self, hs: "HomeServer"):
        JsonResource.__init__(self, hs, canonical_json=False, extract_context=True)
        self.register_servlets(self, hs)

    @staticmethod
    def register_servlets(resource: HttpServer, hs: "HomeServer") -> None:
        ReadinessServlet(hs).register(resource)


class ReadinessServlet(RestServlet):
    """Servlet that reports service readiness."""

    PATTERNS = [re.compile("^/ready$")]
    CATEGORY = "Readiness requests"

    def __init__(self, hs: "HomeServer"):
        super().__init__()
        self.db_pool = hs.get_datastores().main.db_pool

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        """Handle ``GET /ready`` asynchronously.

        Args:
            request: Incoming Synapse request object.

        Returns:
            A tuple containing HTTP status code and readiness payload.
        """
        return await self._build_readiness_payload()

    async def _build_readiness_payload(self) -> Tuple[int, JsonDict]:
        """Run readiness checks and construct the JSON response payload.

        Returns:
            A tuple containing HTTP status code and readiness payload.
        """
        try:
            await self.db_pool.runInteraction(
                "readiness_db_check",
                self._db_check_txn,
                db_autocommit=True,
            )

            return HTTPStatus.OK, {
                "ready": True,
                "checks": {
                    "database": "ok",
                },
            }
        except Exception:
            return HTTPStatus.SERVICE_UNAVAILABLE, {
                "ready": False,
                "checks": {
                    "database": "failed",
                },
                "error": "database query failed",
            }

    @staticmethod
    def _db_check_txn(txn) -> Dict[str, Any]:
        """Execute a minimal SQL round-trip in a transaction.

        Args:
            txn: Database transaction object used by the interaction runner.

        Returns:
            A dictionary indicating a successful database check.

        Raises:
            RuntimeError: If the query result is missing or unexpected.
        """
        txn.execute("SELECT 1")
        row = txn.fetchone()
        if not row or row[0] != 1:
            raise RuntimeError("Unexpected result from readiness query")
        return {"ok": True}
