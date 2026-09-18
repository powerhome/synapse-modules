"""add_index_events_latest_per_room

Revision ID: 06604b735dbb
Revises: f4f3cab478a6
Create Date: 2025-09-03 14:32:56.880072+00:00

"""

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "06604b735dbb"
down_revision: Union[str, None] = "f4f3cab478a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.commit()
    connection.execution_options(isolation_level="AUTOCOMMIT")

    connection.execute(
        text(
            """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_latest_per_room
        ON events (room_id, stream_ordering DESC)
        INCLUDE (event_id, type, sender, origin_server_ts)
        WHERE NOT outlier
          AND type IN ('m.room.message','m.room.join_rules','m.room.create')
    """
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.commit()
    connection.execution_options(isolation_level="AUTOCOMMIT")

    connection.execute(text("DROP INDEX IF EXISTS idx_events_latest_per_room"))
