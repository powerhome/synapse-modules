"""backfill people conversations table

Revision ID: f4f3cab478a6
Revises: 7b10ee4922e2
Create Date: 2024-12-18 13:40:17.299613+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4f3cab478a6"
down_revision: Union[str, None] = "7b10ee4922e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO
            connect.people_conversations
        WITH
            people_conversations_from_ad AS (
                SELECT
                    room_id
                FROM
                    account_data,
                    jsonb_each(content::jsonb) AS each_entry (content_user_id, content_room_ids),
                    jsonb_array_elements_text(content_room_ids) AS room_id
                WHERE
                    account_data_type = 'm.direct'
                GROUP BY
                    room_id
            ),
            people_conversations_from_lcm AS (
                SELECT
                    room_id,
                    array_agg(DISTINCT user_id ORDER BY user_id) AS all_members,
                    sum(event_stream_ordering) AS stream_ordering
                FROM
                    local_current_membership
                GROUP BY
                    room_id
                HAVING
                    count(DISTINCT user_id) >= 2
            ),
            people_conversations AS (
                SELECT DISTINCT
                    lcm.all_members AS members,
                    first_value(ad.room_id) OVER (
                        PARTITION BY
                            lcm.all_members
                        ORDER BY
                            stream_ordering DESC
                    ) AS room_id
                FROM
                    people_conversations_from_ad ad
                    JOIN people_conversations_from_lcm lcm ON ad.room_id = lcm.room_id
            )
        SELECT
            pc.members,
            r.creator,
            e.state_key AS first_invitee,
            pc.room_id
        FROM
            people_conversations pc
            JOIN rooms r ON pc.room_id = r.room_id,
            LATERAL (
                SELECT
                    state_key,
                    room_id
                FROM
                    events
                WHERE
                    pc.room_id = room_id
                    AND type = 'm.room.member'
                ORDER BY
                    origin_server_ts
                OFFSET 1
                LIMIT 1
            ) e
        """
    )


def downgrade() -> None:
    op.execute("TRUNCATE connect.people_conversations")
