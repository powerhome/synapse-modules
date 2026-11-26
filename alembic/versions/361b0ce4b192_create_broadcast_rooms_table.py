"""create broadcast rooms table

Revision ID: 361b0ce4b192
Revises: 
Create Date: 2024-04-30 14:28:41.921444+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "361b0ce4b192"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broadcast_rooms",
        sa.Column("room_id", sa.Text, primary_key=True),
        sa.Column("broadcasters", sa.ARRAY(sa.Text), nullable=False),
        schema="connect",
    )


def downgrade() -> None:
    op.drop_table("broadcast_rooms", schema="connect")
