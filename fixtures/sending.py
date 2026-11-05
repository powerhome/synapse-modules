"""Sends a fixtures message so that sending it again is harmless."""

from synapse.events import EventBase
from synapse.types import JsonDict, create_requester

from connect.fixtures.users import FIXTURES_DEVICE


async def send_once(
    event_creation_handler, sender: str, event_dict: JsonDict, txn_id: str
) -> EventBase:
    """Send ``event_dict`` as ``sender``, or return the event already sent.

    Synapse records ``(room, sender, device, txn_id)`` with the event, in the
    same transaction, and answers a repeat with the original. A step re-run
    after a restart therefore finds its message instead of posting it twice.

    Args:
        event_creation_handler: Synapse's event-creation handler.
        sender (str): The provisioned user sending it.
        event_dict (JsonDict): The event to send.
        txn_id (str): Unique to this deployment step.

    Returns:
        EventBase: The sent (or previously sent) event.
    """
    event, _ = await event_creation_handler.create_and_send_nonmember_event(
        create_requester(sender, device_id=FIXTURES_DEVICE),
        event_dict,
        ratelimit=False,
        txn_id=txn_id,
    )
    return event
