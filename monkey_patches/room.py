"""Room-related monkey patches."""

from typing import Optional, Tuple

from synapse.types import RoomAlias

from . import (
    after_create_room_callbacks,
    before_create_room_callbacks,
    on_create_room_errbacks,
)


class RoomPatches:
    """A module that customizes Synapse rooms.

    Synapse finds and loads this module using the configuration in `homeserver.yaml`.
    """

    def __init__(self, config: dict, api) -> None:
        """Initialize a new instance.

        Args:
            config (dict):
                The values obtained from `homeserver.yaml` for this module.
            api:
                An instance of `synapse.module_api.ModuleApi`
                that enables this module to communicate with Synapse.
        """
        pass

    @staticmethod
    def parse_config(config: dict):
        """Perform post-processing on `homeserver.yaml` configuration.

        Args:
            config (dict):
                The values obtained from `homeserver.yaml` for this module.

        Returns:
            The post-processed configuration.
        """
        return config


def get_room_creation_handler_class():
    """Get `RoomCreationHandler` class from Synapse.

    Note: unit tests do not use the real class.

    Returns:
        `synapse.handlers.room.RoomCreationHandler` if Synapse is available
        `None` otherwise (such as when running tests)
    """
    try:
        from synapse.handlers.room import RoomCreationHandler

        return RoomCreationHandler
    except ImportError:
        return None


if room_creation_handler_class := get_room_creation_handler_class():
    create_room_original = room_creation_handler_class.create_room
else:
    create_room_original = None


async def create_room(
    self,
    requester,
    config,
    ratelimit: bool = True,
    creator_join_profile: Optional = None,
) -> Tuple[str, Optional[RoomAlias], int]:
    """Call `RoomCreationHandler.create_room` followed by a post-processing step.

    See
    https://github.com/matrix-org/synapse/blob/d0b294/synapse/handlers/room.py#L576.

    Args:
        self:
            An instance of `RoomCreationHandler`.
        requester (Requester):
            See `RoomCreationHandler.create_room`.
        config (JsonDict):
            See `RoomCreationHandler.create_room`.
        ratelimit (bool):
            See `RoomCreationHandler.create_room`.
        creator_join_profile (Optional):
            See `RoomCreationHandler.create_room`.

    Returns:
        See `RoomCreationHandler.create_room`.

    Raises:
        Exception: if something goes wrong in create_room_original
    """
    for callback in before_create_room_callbacks:
        await callback(requester.user, config)

    try:
        room_id, room_alias, last_stream_id = await create_room_original(
            self,
            requester,
            config,
            ratelimit=False,
            creator_join_profile=creator_join_profile,
        )
    except Exception:
        for errback in on_create_room_errbacks:
            await errback(requester.user, config)
        raise

    for callback in after_create_room_callbacks:
        await callback(requester.user, room_id, config)

    return room_id, room_alias, last_stream_id


if room_creation_handler_class := get_room_creation_handler_class():
    room_creation_handler_class.create_room = create_room
