"""Badge handler."""

import logging
from typing import TYPE_CHECKING, Optional, Tuple

from synapse.api.constants import ReceiptTypes
from synapse.storage.database import LoggingTransaction
from synapse.storage.engines._base import IsolationLevel
from synapse.types import UserID

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class BadgeHandler:
    """A handler for badges."""

    def __init__(self, hs: "HomeServer"):
        self.db_pool = hs.get_datastores().main.db_pool
        self.receipts_handler = hs.get_receipts_handler()

    async def fix_count(self, user_id: UserID, room_id: str):
        correct, latest_message_event_id = await self.db_pool.runInteraction(
            "has_correct_count",
            self._has_correct_count_txn,
            user_id.to_string(),
            room_id,
        )
        if correct:
            return

        # Using READ_COMMITTED resolves the "could not serialize access due to concurrent delete"
        # errors that happen when the client makes a POST /receipt request at the same time.
        await self.db_pool.runInteraction(
            "delete_receipt",
            self._delete_receipt_txn,
            user_id.to_string(),
            room_id,
            isolation_level=IsolationLevel.READ_COMMITTED,
        )

        await self.db_pool.simple_update(
            table="event_push_summary",
            keyvalues={"user_id": user_id.to_string(), "room_id": room_id},
            updatevalues={"notif_count": 0},
            desc="zero_out_notif_count",
        )

        if latest_message_event_id:
            # The client is always supposed to make a POST /receipt request,
            # but sometimes it doesn't, so the server does it instead.
            await self.receipts_handler.received_client_receipt(
                room_id=room_id,
                receipt_type=ReceiptTypes.READ,
                user_id=user_id,
                event_id=latest_message_event_id,
                thread_id=None,
            )

    def _get_notif_count_txn(
        self, txn: LoggingTransaction, user_id: str, room_id: str
    ) -> Optional[int]:
        sql = """SELECT notif_count FROM event_push_summary WHERE user_id = ? AND room_id = ?"""
        txn.execute(sql, (user_id, room_id))
        row = txn.fetchone()
        if not row:
            return None
        return row[0]

    def _get_latest_message_event_id_txn(
        self, txn: LoggingTransaction, room_id: str
    ) -> Optional[str]:
        sql = """SELECT event_id FROM events WHERE room_id = ? AND type = 'm.room.message' ORDER BY stream_ordering DESC LIMIT 1"""
        txn.execute(sql, (room_id,))
        row = txn.fetchone()
        if not row:
            return None
        return row[0]

    def _has_correct_count_txn(
        self, txn: LoggingTransaction, user_id: str, room_id: str
    ) -> Tuple[bool, Optional[str]]:
        notif_count = self._get_notif_count_txn(txn, user_id, room_id)
        if notif_count == 0:
            return True, None

        latest_message_event_id = self._get_latest_message_event_id_txn(txn, room_id)
        return False, latest_message_event_id

    def _delete_receipt_txn(self, txn: LoggingTransaction, user_id: str, room_id: str):
        keyvalues = {
            "room_id": room_id,
            "receipt_type": ReceiptTypes.READ,
            "user_id": user_id,
        }

        # We delete the receipt records, so that the upcoming POST /receipt request
        # completes and causes clients to sync the latest notif_count (of 0).
        #
        # Synapse only uses receipts_linearized to decide whether to handle a
        # POST /receipt request, so deleting the receipts_graph record is optional.
        # However, if the POST /receipt request were to fail, the two tables would
        # temporarily differ. Thus we delete the record from both tables.
        self.db_pool.simple_delete_txn(
            txn=txn, table="receipts_graph", keyvalues=keyvalues
        )
        self.db_pool.simple_delete_txn(
            txn=txn, table="receipts_linearized", keyvalues=keyvalues
        )
