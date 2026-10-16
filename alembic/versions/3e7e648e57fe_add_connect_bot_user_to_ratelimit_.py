"""Add connect_bot user to ratelimit_override table

Revision ID: 3e7e648e57fe
Revises: 361b0ce4b192
Create Date: 2024-07-24 14:38:54.569542+00:00

"""
from os import getenv
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3e7e648e57fe"
down_revision: Union[str, None] = "361b0ce4b192"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    mxid = getenv("BRIDGE_BOT")
    sql_statement = """
        INSERT INTO public.ratelimit_override (user_id, messages_per_second, burst_count)
        VALUES (:mxid, 0, 0)
        ON CONFLICT (user_id) DO NOTHING;
    """
    op.execute(sa.text(sql_statement).bindparams(mxid=mxid))


def downgrade() -> None:
    mxid = getenv("BRIDGE_BOT")
    sql_statement = """
        DELETE FROM public.ratelimit_override WHERE user_id = :mxid;
    """
    op.execute(sa.text(sql_statement).bindparams(mxid=mxid))
