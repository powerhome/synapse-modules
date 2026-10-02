"""Add audiences_bot user to ratelimit_override table

Revision ID: 64ed72cd2f5b
Revises: 3e7e648e57fe
Create Date: 2024-07-24 14:41:31.921821+00:00

"""
from os import getenv
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "64ed72cd2f5b"
down_revision: Union[str, None] = "3e7e648e57fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    mxid = getenv("AUDIENCES_BOT")
    sql_statement = """
        INSERT INTO public.ratelimit_override (user_id, messages_per_second, burst_count)
        VALUES (:mxid, 0, 0)
        ON CONFLICT (user_id) DO NOTHING;
    """
    op.execute(sa.text(sql_statement).bindparams(mxid=mxid))


def downgrade() -> None:
    mxid = getenv("AUDIENCES_BOT")
    sql_statement = """
        DELETE FROM public.ratelimit_override WHERE user_id = :mxid;
    """
    op.execute(sa.text(sql_statement).bindparams(mxid=mxid))
