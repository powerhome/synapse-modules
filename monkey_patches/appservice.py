"""ApplicationServicesHandler monkey patches."""

from typing import Collection, Union

from synapse.handlers.appservice import ApplicationServicesHandler
from synapse.types import MultiWriterStreamToken, RoomStreamToken, StreamKeyType, UserID


class NotifyInterestedServicesEphemeralPatch:
    """A class containing patches for the ApplicationServicesHandler"""

    def __init__(self, config: dict, api) -> None:
        pass


# Patched: ONLY send receipt ephemeral events
def patched_notify_interested_services_ephemeral(
    self,
    stream_key: StreamKeyType,
    new_token: Union[int, RoomStreamToken, MultiWriterStreamToken],
    users: Collection[Union[str, UserID]],
) -> None:
    """
    This is called by the notifier in the background when an ephemeral event is handled by the homeserver.

    This will determine which appservices are interested in the event, and submit them.

    Args:
        self: The instance of the class.
        stream_key: The stream the event came from.
        new_token: The stream token of the event.
        users: The users that should be informed of the new event, if any.
    """
    # Patched: ONLY send receipt ephemeral events
    if not self.notify_appservices:
        return

    if stream_key != StreamKeyType.RECEIPT:
        return

    assert not isinstance(new_token, RoomStreamToken)

    services = self.store.get_app_services()
    services = [
        service
        for service in services
        if stream_key == StreamKeyType.RECEIPT and service.supports_ephemeral
    ]

    if not services:
        return

    self._notify_interested_services_ephemeral(services, stream_key, new_token, users)


ApplicationServicesHandler.notify_interested_services_ephemeral = (
    patched_notify_interested_services_ephemeral
)
