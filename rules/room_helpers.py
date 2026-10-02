"""Room-related helper functions."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synapse.server import HomeServer


async def is_user_in_room(hs: "HomeServer", user_id: str, room_id: str):
    store = hs.get_datastores().main
    user_ids = await store.get_users_in_room(room_id)
    if user_id in set(user_ids):
        return True
    return False
