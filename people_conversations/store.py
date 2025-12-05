"""People conversations store."""

import logging
from http import HTTPStatus

from psycopg2.errors import CheckViolation, UniqueViolation
from synapse.api.errors import Codes, StoreError
from synapse.storage.database import DatabasePool, LoggingTransaction

logger = logging.getLogger(__name__)


class PeopleConversationStore:
    """A class that handles people conversation database queries."""

    def __init__(self, db_pool: DatabasePool):
        self.db_pool = db_pool

    async def store_draft_people_conversation(
        self, creator: str, first_invitee: str, members: list[str]
    ):
        members = list(set(members))

        try:
            await self.db_pool.simple_insert(
                "connect.people_conversations",
                {
                    "members": members,
                    "creator": creator,
                    "first_invitee": first_invitee,
                },
            )
        except UniqueViolation:
            message = f"people conversation ({members}) already exists"
            logger.error(message)
            raise StoreError(HTTPStatus.CONFLICT, message)
        except CheckViolation:
            message = (
                f"people conversation ({members}) has too many or too few invitees"
            )
            logger.error(message)
            raise StoreError(HTTPStatus.BAD_REQUEST, message)

    async def delete_draft_people_conversation(self, members: list[str]):
        members = list(set(members))

        def delete(txn: LoggingTransaction):
            sql = "DELETE FROM connect.people_conversations WHERE members = ARRAY(SELECT unnest(?) ORDER BY 1) AND room_id IS NULL"
            txn.execute(sql, (members,))

        await self.db_pool.runInteraction(
            "delete_draft_people_conversation", delete, db_autocommit=True
        )

    async def set_people_conversation_id(self, members: list[str], room_id: str):
        members = list(set(members))

        def update(txn: LoggingTransaction) -> int:
            sql = "UPDATE connect.people_conversations SET room_id = ? WHERE members = ARRAY(SELECT unnest(?) ORDER BY 1)"
            txn.execute(sql, (room_id, members))
            return txn.rowcount

        count = await self.db_pool.runInteraction(
            "set_people_conversation_id", update, db_autocommit=True
        )
        assert count == 1, f"people conversation ({members}) does not exist"

    async def get_by_members(self, members: list[str]) -> str:
        members = list(set(members))

        if len(members) < 2:
            raise StoreError(
                HTTPStatus.NOT_FOUND,
                "people conversations have at least 2 members",
                Codes.NOT_FOUND,
            )
        if len(members) > 11:
            raise StoreError(
                HTTPStatus.NOT_FOUND,
                "people conversations have at most 11 members",
                Codes.NOT_FOUND,
            )

        def select(txn: LoggingTransaction):
            sql = "SELECT room_id FROM connect.people_conversations WHERE members = ARRAY(SELECT unnest(?) ORDER BY 1) AND room_id IS NOT NULL"
            txn.execute(sql, (members,))
            return [r[0] for r in txn]

        rooms = await self.db_pool.runInteraction(
            "get_by_members", select, db_autocommit=True
        )

        if not rooms:
            raise StoreError(
                HTTPStatus.NOT_FOUND,
                "people conversation does not exist",
                Codes.NOT_FOUND,
            )

        return rooms[0]

    async def is_people_conversation(self, room_id: str) -> bool:
        members = await self.db_pool.simple_select_one_onecol(
            "connect.people_conversations",
            keyvalues={"room_id": room_id},
            retcol="members",
            allow_none=True,
        )
        return members is not None
