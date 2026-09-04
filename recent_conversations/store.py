"""Store for recent conversations functionality."""

from dataclasses import asdict, dataclass


@dataclass
class LatestEvent:
    """Represents the latest event data for a room."""

    event_id: str
    type: str
    sender: str
    origin_server_ts: int

    def to_dict(self) -> dict:
        """Convert the LatestEvent to a dictionary.

        Returns:
            dict: Dictionary representation of the LatestEvent.
        """
        return asdict(self)


class LatestEventsResult:
    """Wrapper for latest events results with convenient serialization methods."""

    def __init__(self, events: dict[str, LatestEvent]):
        self._events = events

    def to_dict(self) -> dict:
        """Convert all events to a response dictionary.

        Returns:
            dict: Dictionary mapping room IDs to their event dictionaries.
        """
        return {k: v.to_dict() for k, v in self._events.items()}


class RecentConversationsStore:
    """Store for fetching recent conversation data."""

    def __init__(self, db_pool):
        """Initialize the store with a database pool.

        Args:
            db_pool: The Synapse database pool instance.
        """
        self.db_pool = db_pool

    async def fetch_latest_events_for_rooms(
        self, room_ids: list[str]
    ) -> LatestEventsResult:
        """Get the latest event data for the given rooms.

        Args:
            room_ids: List of room IDs to fetch event data for.

        Returns:
            LatestEventsResult containing room IDs mapped to their latest event data.
        """
        if not room_ids:
            return LatestEventsResult({})

        events = await self._fetch(room_ids)
        return LatestEventsResult(events)

    async def _fetch(self, room_ids: list[str]) -> dict[str, LatestEvent]:
        """Execute the database query to fetch latest events for the given room IDs.

        Args:
            room_ids: List of room IDs to fetch event data for.

        Returns:
            Dictionary mapping room IDs to their latest event data.
        """

        def _fetch_latest_events_txn(txn):
            sql = """
              WITH ranked_events AS (
                SELECT
                  room_id,
                  event_id,
                  type,
                  sender,
                  origin_server_ts,
                  ROW_NUMBER() OVER (
                    PARTITION BY room_id
                    ORDER BY stream_ordering DESC
                  ) as rn
                  FROM events
                  WHERE room_id = ANY(%(room_ids)s)
                    AND type IN ('m.room.message', 'm.room.join_rules', 'm.room.create')
                    AND NOT outlier
              )
              SELECT room_id, event_id, type, sender, origin_server_ts
              FROM ranked_events
              WHERE rn = 1
            """  # noqa: S608

            txn.execute(sql, {"room_ids": room_ids})

            results = {}

            for room_id, event_id, event_type, sender, timestamp in txn:
                results[room_id] = LatestEvent(
                    event_id=event_id,
                    type=event_type,
                    sender=sender,
                    origin_server_ts=timestamp,
                )

            return results

        return await self.db_pool.runInteraction(
            "fetch_latest_events_for_rooms", _fetch_latest_events_txn
        )
