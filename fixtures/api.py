"""HTTP routes for the fixtures service."""

import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Tuple

from synapse.api.errors import Codes, SynapseError
from synapse.http.server import JsonResource
from synapse.http.servlet import RestServlet, assert_params_in_dict, parse_integer
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.types import JsonDict

from connect.fixtures.provisioner import Provisioner
from connect.fixtures.receipts import ReceiptProvisioner
from connect.fixtures.runner import DeploymentRunner
from connect.helpers.request_context import init_request_context, log_event

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class FixturesResource(JsonResource):
    """JSON resource hosting the deployment, inventory and teardown servlets."""

    def __init__(
        self, hs: "HomeServer", provisioner: Provisioner, runner: DeploymentRunner
    ):
        JsonResource.__init__(self, hs, canonical_json=False)
        DeploymentsServlet(provisioner, runner).register(self)
        DeploymentServlet(provisioner, runner).register(self)
        CredentialsServlet(provisioner, runner).register(self)
        CleanupServlet(provisioner, runner).register(self)
        FixturesRootServlet(provisioner, runner).register(self)


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


class DeploymentsServlet(RestServlet):
    """Provisions a scenario: the one way to create fixtures.

    ``POST /_fixtures/deployments?wait=<seconds>``

    The body is the scenario, plus an optional ``key``: re-sending a key
    returns the deployment it first admitted, so a caller whose connection
    dropped (during a redeploy, say) simply retries. With ``wait``, the
    request holds until the deployment finishes (up to a cap): ``200`` with the
    provisioned graph and its credentials, or the deployment's error. A
    deployment still running when the wait ends answers ``202
    {deployment_id}``; poll ``GET /_fixtures/deployments/{deployment_id}``.
    """

    PATTERNS = [re.compile("^/_fixtures/deployments$")]
    CATEGORY = "Fixtures"

    def __init__(self, provisioner: Provisioner, runner: DeploymentRunner):
        super().__init__()
        self.provisioner = provisioner
        self.runner = runner

    async def on_POST(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="start_deployment")

        body = parse_json_object_from_request(request)
        key = body.get("key")
        scenario = {name: value for name, value in body.items() if name != "key"}
        wait_s = parse_integer(request, "wait", default=0)

        record = await self.runner.admit(scenario, key)
        if wait_s > 0:
            record = await self.runner.wait(record.id, wait_s)
        if record.is_unfinished:
            return HTTPStatus.ACCEPTED, {
                "deployment_id": record.id,
                "status": record.status,
            }
        if record.status != "complete":
            # A deployment that failed within the wait answers like the request
            # itself failed, so a waiting caller handles one error path.
            raise SynapseError(
                record.error_code or HTTPStatus.INTERNAL_SERVER_ERROR,
                record.error or f"deployment {record.status}",
                record.errcode or Codes.UNKNOWN,
                additional_fields={
                    "deployment_id": record.id,
                    "status": record.status,
                },
            )
        return HTTPStatus.OK, await self.runner.report(record, collect=True)


class DeploymentServlet(RestServlet):
    """One deployment.

    ``GET /_fixtures/deployments/{deployment_id}`` — its status and progress;
    once complete, the provisioned graph, with credentials on the first read.
    ``DELETE /_fixtures/deployments/{deployment_id}`` — cancel it if unfinished,
    deprovision it if complete; either way only its own resources go.
    """

    PATTERNS = [re.compile("^/_fixtures/deployments/(?P<deployment_id>[^/]+)$")]
    CATEGORY = "Fixtures"

    def __init__(self, provisioner: Provisioner, runner: DeploymentRunner):
        super().__init__()
        self.provisioner = provisioner
        self.runner = runner

    async def on_GET(
        self, request: SynapseRequest, deployment_id: str
    ) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="deployment_status")

        record = _found(await self.runner.get(deployment_id))
        return HTTPStatus.OK, await self.runner.report(record, collect=True)

    async def on_DELETE(
        self, request: SynapseRequest, deployment_id: str
    ) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="delete_deployment")

        return HTTPStatus.ACCEPTED, _found(await self.runner.delete(deployment_id))


class CredentialsServlet(RestServlet):
    """Mints fresh credentials for a complete deployment's users.

    ``POST /_fixtures/deployments/{deployment_id}/credentials`` — for a caller
    that lost the ones it collected: new passwords, new login tokens.
    """

    PATTERNS = [
        re.compile("^/_fixtures/deployments/(?P<deployment_id>[^/]+)/credentials$")
    ]
    CATEGORY = "Fixtures"

    def __init__(self, provisioner: Provisioner, runner: DeploymentRunner):
        super().__init__()
        self.provisioner = provisioner
        self.runner = runner

    async def on_POST(
        self, request: SynapseRequest, deployment_id: str
    ) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="reissue_credentials")

        return HTTPStatus.OK, _found(
            await self.runner.reissue_credentials(deployment_id)
        )


class CleanupServlet(RestServlet):
    """Tears down everything provisioned for a run.

    ``DELETE /_fixtures/run/{run_id}`` — refused (409) while any deployment of
    the run is unfinished.
    """

    PATTERNS = [re.compile("^/_fixtures/run/(?P<run_id>[^/]+)$")]
    CATEGORY = "Fixtures"

    def __init__(self, provisioner: Provisioner, runner: DeploymentRunner):
        super().__init__()
        self.provisioner = provisioner
        self.runner = runner

    async def on_DELETE(
        self, request: SynapseRequest, run_id: str
    ) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="cleanup_run")

        result = await self.runner.teardown(run_id)
        log_event(logger, "Cleaned up run", run_id=run_id, **result)
        return HTTPStatus.OK, {"run_id": run_id, **result}


class FixturesRootServlet(RestServlet):
    """The fixtures root resource, across all runs.

    ``GET /_fixtures`` — list every run currently up and the resources it owns.
    ``DELETE /_fixtures`` — tear down every fixtures resource, across all runs;
    refused (409) while any deployment is unfinished.
    """

    # Anchored to the bare path so it can't shadow ``/_fixtures/deployments``
    # or ``/_fixtures/run/{run_id}`` (a trailing slash is tolerated).
    PATTERNS = [re.compile("^/_fixtures/?$")]
    CATEGORY = "Fixtures"

    def __init__(self, provisioner: Provisioner, runner: DeploymentRunner):
        super().__init__()
        self.provisioner = provisioner
        self.runner = runner

    async def on_GET(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="inventory")

        result = await self.provisioner.inventory()
        log_event(logger, "Listed fixtures inventory", run_count=len(result["runs"]))
        return HTTPStatus.OK, result

    async def on_DELETE(self, request: SynapseRequest) -> Tuple[int, JsonDict]:
        self.provisioner.authenticate(request)
        init_request_context(request, action="cleanup_all")

        result = await self.runner.teardown(None)
        log_event(logger, "Cleaned up all fixtures resources", **result)
        return HTTPStatus.OK, result


def _found(result):
    if result is None:
        raise SynapseError(
            HTTPStatus.NOT_FOUND, "no such fixtures deployment", Codes.NOT_FOUND
        )
    return result
