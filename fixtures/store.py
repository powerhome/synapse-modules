"""The durable record of fixtures deployments (``connect.fixture_deployments``)."""

import json
from dataclasses import dataclass

from synapse.types import JsonDict

TABLE = "connect.fixture_deployments"

# A deployment in one of these states still owns work, or is about to.
UNFINISHED = ("queued", "running", "deprovisioning")

COLUMNS = (
    "id",
    "key",
    "run_id",
    "status",
    "cancel_requested",
    "credentials_claimed",
    "scenario",
    "checkpoint",
    "fixed_localparts",
    "error",
    "error_code",
    "errcode",
    "created_ms",
    "heartbeat_ms",
    "finished_ms",
)


@dataclass(frozen=True)
class DeploymentRecord:
    """One row of ``connect.fixture_deployments``, JSON columns decoded."""

    id: str
    key: str | None
    run_id: str
    status: str
    cancel_requested: bool
    credentials_claimed: bool
    scenario: JsonDict
    checkpoint: JsonDict | None
    fixed_localparts: list[str]
    error: str | None
    error_code: int | None
    errcode: str | None
    created_ms: int
    heartbeat_ms: int | None
    finished_ms: int | None

    @property
    def is_unfinished(self) -> bool:
        """Whether the deployment still owns, or is about to start, work.

        Returns:
            bool: True while queued, running or deprovisioning.
        """
        return self.status in UNFINISHED

    @classmethod
    def from_row(cls, row: dict) -> "DeploymentRecord":
        """Decode a database row.

        Args:
            row (dict): Column name -> stored value.

        Returns:
            DeploymentRecord: The decoded record.
        """
        return cls(
            **{
                **row,
                "cancel_requested": bool(row["cancel_requested"]),
                "credentials_claimed": bool(row["credentials_claimed"]),
                "scenario": json.loads(row["scenario"]),
                "checkpoint": (
                    json.loads(row["checkpoint"]) if row["checkpoint"] else None
                ),
                "fixed_localparts": json.loads(row["fixed_localparts"]),
            }
        )


class DeploymentStore:
    """Reads and writes fixtures deployments through Synapse's database pool."""

    def __init__(self, hs):
        self.db_pool = hs.get_datastores().main.db_pool

    async def insert(
        self,
        deployment_id: str,
        key: str | None,
        run_id: str,
        scenario: JsonDict,
        fixed_localparts: list[str],
        created_ms: int,
    ) -> None:
        """Record a newly admitted deployment as ``queued``.

        Args:
            deployment_id (str): The new deployment's id.
            key (str | None): The caller's idempotency key, if any.
            run_id (str): The run that owns what it provisions.
            scenario (JsonDict): The scenario it provisions.
            fixed_localparts (list[str]): The Fixed localparts it reserves.
            created_ms (int): When it was admitted.
        """
        await self.db_pool.simple_insert(
            table=TABLE,
            values={
                "id": deployment_id,
                "key": key,
                "run_id": run_id,
                "status": "queued",
                "cancel_requested": False,
                "credentials_claimed": False,
                "scenario": json.dumps(scenario),
                "fixed_localparts": json.dumps(fixed_localparts),
                "created_ms": created_ms,
            },
            desc="fixtures_insert_deployment",
        )

    async def get(self, deployment_id: str) -> DeploymentRecord | None:
        """The deployment ``deployment_id``, or ``None``.

        Args:
            deployment_id (str): The deployment's id.

        Returns:
            DeploymentRecord | None: The record, if it exists.
        """
        return await self._select_one({"id": deployment_id})

    async def get_by_key(self, key: str) -> DeploymentRecord | None:
        """The deployment admitted under the idempotency ``key``, or ``None``.

        Args:
            key (str): The caller's idempotency key.

        Returns:
            DeploymentRecord | None: The record, if one exists.
        """
        return await self._select_one({"key": key})

    async def unfinished(self, run_id: str | None = None) -> list[DeploymentRecord]:
        """Every unfinished deployment, oldest first, optionally of one run.

        Args:
            run_id (str | None): Only deployments of this run, if given.

        Returns:
            list[DeploymentRecord]: The unfinished deployments.
        """
        rows = await self.db_pool.runInteraction(
            "fixtures_select_unfinished_deployments",
            self._select_unfinished_txn,
            run_id,
        )
        return [DeploymentRecord.from_row(row) for row in rows]

    async def save_checkpoint(
        self, deployment_id: str, checkpoint: JsonDict, heartbeat_ms: int
    ) -> None:
        """Record how far a deployment has got.

        Args:
            deployment_id (str): The deployment's id.
            checkpoint (JsonDict): Its checkpoint.
            heartbeat_ms (int): When the checkpoint was taken.
        """
        await self._update(
            deployment_id,
            {"checkpoint": json.dumps(checkpoint), "heartbeat_ms": heartbeat_ms},
        )

    async def set_status(
        self,
        deployment_id: str,
        status: str,
        *,
        now_ms: int,
        error: str | None = None,
        error_code: int | None = None,
        errcode: str | None = None,
    ) -> None:
        """Move a deployment to ``status``, finishing it unless still unfinished.

        Args:
            deployment_id (str): The deployment's id.
            status (str): Its new status.
            now_ms (int): The current time.
            error (str | None): Why it failed, if it did.
            error_code (int | None): The HTTP status of that failure.
            errcode (str | None): The Matrix errcode of that failure.
        """
        values = {
            "status": status,
            "heartbeat_ms": now_ms,
            "error": error,
            "error_code": error_code,
            "errcode": errcode,
        }
        if status not in UNFINISHED:
            values["finished_ms"] = now_ms
        await self._update(deployment_id, values)

    async def request_cancel(self, deployment_id: str) -> None:
        """Flag a deployment to stop between steps (it survives restarts).

        Args:
            deployment_id (str): The deployment's id.
        """
        await self._update(deployment_id, {"cancel_requested": True})

    async def claim_credentials(self, deployment_id: str) -> bool:
        """Mark a deployment's credentials collected, once.

        Args:
            deployment_id (str): The deployment's id.

        Returns:
            bool: True for the one caller that claimed them, False after.
        """
        return await self.db_pool.runInteraction(
            "fixtures_claim_credentials", self._claim_credentials_txn, deployment_id
        )

    async def mark_deprovisioned(self, run_id: str | None, now_ms: int) -> None:
        """Mark completed deployments deprovisioned after a run/global teardown.

        Args:
            run_id (str | None): The torn-down run, or ``None`` for every run.
            now_ms (int): The current time.
        """
        await self.db_pool.runInteraction(
            "fixtures_mark_deprovisioned",
            self._mark_deprovisioned_txn,
            run_id,
            now_ms,
        )

    async def _select_one(self, keyvalues: dict) -> DeploymentRecord | None:
        row = await self.db_pool.simple_select_one(
            table=TABLE,
            keyvalues=keyvalues,
            retcols=COLUMNS,
            allow_none=True,
            desc="fixtures_select_deployment",
        )
        if row is None:
            return None
        return DeploymentRecord.from_row(dict(zip(COLUMNS, row)))

    async def _update(self, deployment_id: str, values: dict) -> None:
        await self.db_pool.simple_update(
            table=TABLE,
            keyvalues={"id": deployment_id},
            updatevalues=values,
            desc="fixtures_update_deployment",
        )

    @staticmethod
    def _select_unfinished_txn(txn, run_id: str | None) -> list[dict]:
        sql = (
            f"SELECT {', '.join(COLUMNS)} FROM {TABLE} "  # noqa: S608
            f"WHERE status IN ({', '.join('?' for _ in UNFINISHED)})"
        )
        args: list = list(UNFINISHED)
        if run_id is not None:
            sql += " AND run_id = ?"
            args.append(run_id)
        txn.execute(sql + " ORDER BY created_ms", args)
        return [dict(zip(COLUMNS, row)) for row in txn]

    @staticmethod
    def _claim_credentials_txn(txn, deployment_id: str) -> bool:
        txn.execute(
            f"UPDATE {TABLE} SET credentials_claimed = TRUE "  # noqa: S608
            "WHERE id = ? AND credentials_claimed = FALSE",
            (deployment_id,),
        )
        return txn.rowcount == 1

    @staticmethod
    def _mark_deprovisioned_txn(txn, run_id: str | None, now_ms: int) -> None:
        sql = (
            f"UPDATE {TABLE} SET status = 'deprovisioned', finished_ms = ? "  # noqa: S608
            "WHERE status = 'complete'"
        )
        args: list = [now_ms]
        if run_id is not None:
            sql += " AND run_id = ?"
            args.append(run_id)
        txn.execute(sql, args)
