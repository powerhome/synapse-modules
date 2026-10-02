"""create profiles table

Revision ID: b1aa5b301312
Revises: 64ed72cd2f5b
Create Date: 2024-09-03 16:24:21.736759+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1aa5b301312"
down_revision: Union[str, None] = "64ed72cd2f5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("data", sa.dialects.postgresql.JSONB, nullable=False),
        schema="connect",
    )


def downgrade() -> None:
    op.drop_table("profiles", schema="connect")
