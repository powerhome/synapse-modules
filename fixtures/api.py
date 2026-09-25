"""HTTP routes for the fixtures service."""

import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.http.server import JsonResource
from synapse.http.servlet import RestServlet, assert_params_in_dict
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.types import JsonDict

from connect.fixtures.provisioner import Provisioner
from connect.fixtures.receipts import ReceiptProvisioner
from connect.helpers.request_context import init_request_context, log_event

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class FixturesResource(JsonResource):
    """JSON resource hosting the scenario and cleanup servlets."""

    def __init__(self, hs: "HomeServer", provisioner: Provisioner):
        JsonResource.__init__(self, hs, canonical_json=False)
        ScenarioServlet(provisioner).register(self)
        CleanupServlet(provisioner).register(self)
        FixturesRootServlet(provisioner).register(self)


class ReceiptWriterResource(JsonResource):
    """JSON resource hosting the receipts-writer receipt servlet."""

    def __init__(self, hs: "HomeServer", receipts: ReceiptProvisioner):
        JsonResource.__init__(self, hs, canonical_json=False)
        ReceiptServlet(receipts).register(self)


class ReceiptServlet(RestServlet):
    """Records a fixtures read receipt on the receipts stream writer.

    ``POST /_fixtures/receipt``

    The scenario servlet runs on the main process, which in worker deployments
    cannot write to the receipts stream; its ``ReceiptProvisioner`` forwards
    each receipt here instead. Registered only on the receipts writer, and only
    where the fixtures module is enabled.
    """

    PATTERNS = [re.compile("^/_fixtures/receipt$")]
    CATEGORY = "Fixtures"

    def __init__(self, receipts: ReceiptProvisioner):
        super().__init__()
        self.receipts = receipts

    async def on_POST(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        self.receipts.authenticate(request)
        init_request_context(request, action="record_receipt")

        body = parse_json_object_from_request(request)
        assert_params_in_dict(body, ["room_id", "user_id", "event_id"])
        await self.receipts.record(
            room_id=body["room_id"],
            user_id=body["user_id"],
            event_id=body["event_id"],
        )
        return HTTPStatus.OK, {}


class ScenarioServlet(RestServlet):
    """Provisions a fixture scenario.

    ``POST /_fixtures/scenario``
    """

    PATTERNS = [re.compile("^/_fixtures/scenario$")]
    CATEGORY = "Fixtures"

    def __init__(self, provisioner: Provisioner):
        super().__init__()
        self.provisioner = provisioner

    async def on_POST(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="provision_scenario")

        scenario = parse_json_object_from_request(request)
        log_event(
            logger,
            "Provisioning scenario",
            run_id=scenario.get("run_id"),
            user_count=len(scenario.get("users", [])),
        )

        result = await self.provisioner.provision_scenario(scenario)
        return HTTPStatus.OK, result


class CleanupServlet(RestServlet):
    """Tears down everything provisioned for a run.

    ``DELETE /_fixtures/run/{run_id}``
    """

    PATTERNS = [re.compile("^/_fixtures/run/(?P<run_id>[^/]+)$")]
    CATEGORY = "Fixtures"

    def __init__(self, provisioner: Provisioner):
        super().__init__()
        self.provisioner = provisioner

    async def on_DELETE(
        self, request: SynapseRequest, run_id: str
    ) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="cleanup_run")

        result = await self.provisioner.cleanup_run(run_id)
        log_event(logger, "Cleaned up run", run_id=run_id, **result)
        return HTTPStatus.OK, {"run_id": run_id, **result}


class FixturesRootServlet(RestServlet):
    """The fixtures root resource, across all runs.

    ``GET /_fixtures`` — list every run currently up and the resources it owns.
    ``DELETE /_fixtures`` — tear down every fixtures resource, across all runs.
    """

    # Anchored to the bare path so it can't shadow ``/_fixtures/scenario`` or
    # ``/_fixtures/run/{run_id}`` (a trailing slash is tolerated).
    PATTERNS = [re.compile("^/_fixtures/?$")]
    CATEGORY = "Fixtures"

    def __init__(self, provisioner: Provisioner):
        super().__init__()
        self.provisioner = provisioner

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="inventory")

        result = await self.provisioner.inventory()
        log_event(logger, "Listed fixtures inventory", run_count=len(result["runs"]))
        return HTTPStatus.OK, result

    async def on_DELETE(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="cleanup_all")

        result = await self.provisioner.cleanup_all()
        log_event(logger, "Cleaned up all fixtures resources", **result)
        return HTTPStatus.OK, result
