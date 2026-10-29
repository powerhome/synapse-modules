"""create audiences evaluation claims table

A cross-worker claim on "room X's audience evaluation is running". Any
Synapse worker may serve an evaluation request (the CPG's PUT, or an
unarchive restore), so the claim must live where every worker can see it —
this table — rather than in per-process memory.

Revision ID: c8d4a91e57f0
Revises: 06604b735dbb
Create Date: 2026-07-13 19:45:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d4a91e57f0"
down_revision: Union[str, None] = "06604b735dbb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audiences_evaluation_claims",
        sa.Column("room_id", sa.Text, primary_key=True),
        sa.Column("token", sa.Text, nullable=False),
        sa.Column("claimed_at_ms", sa.BigInteger, nullable=False),
        sa.Column("expires_at_ms", sa.BigInteger, nullable=False),
        sa.Column(
            "rerun_requested",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        schema="connect",
    )


def downgrade() -> None:
    op.drop_table("audiences_evaluation_claims", schema="connect")
