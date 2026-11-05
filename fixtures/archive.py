"""Archives rooms through Connect's own archive handler."""

import logging

from synapse.types import create_requester

from connect.archive_rooms.handler import ArchiveRoomHandler

logger = logging.getLogger(__name__)


class RoomArchiver:
    """Archives a seeded room exactly as Connect's archive API does.

    It drives the real ``ArchiveRoomHandler`` rather than re-implementing it, so a
    seeded archived room is indistinguishable from a real one: the room is
    blocked (which makes the archive module's spam checker reject every later
    event), every local member except the archiver and the bots is parted and
    forgotten, and a public room drops out of the public directory. Because a
    blocked room accepts no more events, a scenario archives last.

    The handler is built without bridge or audiences-restore config, so seeding
    never sends the handler's bridge notification.
    """

    def __init__(self, hs, bot_user_ids: list[str]):
        self._handler = ArchiveRoomHandler(
            hs,
            hs.get_module_api(),
            {"bot_user_ids": bot_user_ids, "audiences_services_enabled": False},
        )

    async def archive(self, room_id: str, archiver_mxid: str) -> None:
        """Archive ``room_id``, keeping ``archiver_mxid`` as its one remaining member.

        Args:
            room_id (str): The room to archive.
            archiver_mxid (str): The user archiving it — the only human who stays in the room.
        """
        await self._handler.handle_put(
            room_id, archive=True, requester=create_requester(archiver_mxid)
        )
        logger.info("fixtures archived room %s", room_id)
