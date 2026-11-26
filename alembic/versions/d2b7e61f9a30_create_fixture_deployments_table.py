"""create fixture deployments table

The durable record of every fixtures deployment: the scenario it provisions,
where it has got to (its checkpoint), and how it ended. A deployment resumes
from its checkpoint after a Synapse restart, so the record must outlive the
process — hence a table, not memory. Only the main process writes it.

Revision ID: d2b7e61f9a30
Revises: c8d4a91e57f0
Create Date: 2026-10-30 18:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2b7e61f9a30"
down_revision: Union[str, None] = "c8d4a91e57f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fixture_deployments",
        sa.Column("id", sa.Text, primary_key=True),
        # A caller-chosen idempotency key: re-POSTing it returns this row.
        sa.Column("key", sa.Text, nullable=True, unique=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column(
            "cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "credentials_claimed",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        # JSON documents, stored as text.
        sa.Column("scenario", sa.Text, nullable=False),
        sa.Column("checkpoint", sa.Text, nullable=True),
        sa.Column("fixed_localparts", sa.Text, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("error_code", sa.Integer, nullable=True),
        sa.Column("errcode", sa.Text, nullable=True),
        sa.Column("created_ms", sa.BigInteger, nullable=False),
        sa.Column("heartbeat_ms", sa.BigInteger, nullable=True),
        sa.Column("finished_ms", sa.BigInteger, nullable=True),
        schema="connect",
    )
    op.create_index(
        "fixture_deployments_status",
        "fixture_deployments",
        ["status", "created_ms"],
        schema="connect",
    )


def downgrade() -> None:
    op.drop_index("fixture_deployments_status", "fixture_deployments", schema="connect")
    op.drop_table("fixture_deployments", schema="connect")
