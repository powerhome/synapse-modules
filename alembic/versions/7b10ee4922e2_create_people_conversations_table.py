"""create people conversations table

Revision ID: 7b10ee4922e2
Revises: b1aa5b301312
Create Date: 2024-12-13 21:27:40.372951+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b10ee4922e2"
down_revision: Union[str, None] = "b1aa5b301312"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "people_conversations",
        sa.Column(
            "members",
            sa.ARRAY(sa.Text),
            sa.CheckConstraint(
                "cardinality(members) >= 2 AND cardinality(members) <= 11"
            ),
            primary_key=True,
        ),
        sa.Column("creator", sa.Text, nullable=False),
        sa.Column("first_invitee", sa.Text, nullable=False),
        sa.Column("room_id", sa.Text, unique=True),
        schema="connect",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sort_members()
        RETURNS TRIGGER AS $$
        BEGIN
        NEW.members := ARRAY(SELECT unnest(NEW.members) ORDER BY 1);
        RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER sort_members_before_insert
        BEFORE INSERT ON connect.people_conversations
        FOR EACH ROW
        EXECUTE FUNCTION sort_members()
        """
    )


def downgrade() -> None:
    op.drop_table("people_conversations", schema="connect")
    op.execute("DROP FUNCTION IF EXISTS sort_members")
