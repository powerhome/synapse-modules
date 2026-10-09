"""Broadcast rooms store."""

from http import HTTPStatus
from typing import Optional

from synapse.api.errors import Codes, StoreError
from synapse.storage.database import DatabasePool, LoggingTransaction


class BroadcastRoomStore:
    """A class that handles broadcast room database queries."""

    def __init__(self, db_pool: DatabasePool):
        self.db_pool = db_pool

    async def get_all_broadcasters(self) -> dict[str, list[str]]:
        return dict(
            await self.db_pool.simple_select_list(
                "connect.broadcast_rooms",
                keyvalues=None,
                retcols=["room_id", "broadcasters"],
            )
        )

    async def get_broadcasters(self, room_id: str) -> Optional[list[str]]:
        return await self.db_pool.simple_select_one_onecol(
            "connect.broadcast_rooms",
            keyvalues={"room_id": room_id},
            retcol="broadcasters",
            allow_none=True,
        )

    async def set_broadcasters(self, room_id: str, broadcasters: list[str]):
        await self.db_pool.runInteraction(
            "set_broadcasters", self._set_broadcasters_txn, room_id, broadcasters
        )

    def _set_broadcasters_txn(
        self, txn: LoggingTransaction, room_id: str, broadcasters: list[str]
    ):
        self._room_exists_txn(txn, room_id)
        self._all_users_exist_txn(txn, broadcasters)

        self.db_pool.simple_upsert_txn(
            txn=txn,
            table="connect.broadcast_rooms",
            keyvalues={"room_id": room_id},
            values={"broadcasters": broadcasters},
        )

    def _room_exists_txn(self, txn: LoggingTransaction, room_id: str):
        room = self.db_pool.simple_select_one_onecol_txn(
            txn=txn,
            table="rooms",
            keyvalues={"room_id": room_id},
            retcol="room_id",
            allow_none=True,
        )
        if not room:
            raise StoreError(
                HTTPStatus.NOT_FOUND, f"{room_id} does not exist", Codes.NOT_FOUND
            )

    def _all_users_exist_txn(self, txn: LoggingTransaction, users: list[str]):
        result = self.db_pool.simple_select_many_txn(
            txn=txn,
            table="users",
            column="name",
            iterable=users,
            keyvalues={},
            retcols=["name"],
        )
        if len(result) != len(users):
            raise StoreError(
                HTTPStatus.NOT_FOUND,
                f"{users} contains users that do not exist",
                Codes.NOT_FOUND,
            )

    async def unbroadcast_room(self, room_id: str):
        try:
            await self.db_pool.simple_delete_one(
                "connect.broadcast_rooms", keyvalues={"room_id": room_id}
            )
        except StoreError as e:
            if e.code == HTTPStatus.NOT_FOUND:
                raise StoreError(
                    HTTPStatus.NOT_FOUND,
                    f"{room_id} is not a broadcast room",
                    Codes.NOT_FOUND,
                )
            else:
                raise e
