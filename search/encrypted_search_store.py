"""
ConnectSearchRestServlet module

Message searching including decryption of encrypted messages
and integration with an in-memory search index.
"""

from typing import Collection, Iterable, Optional

from synapse.storage.databases.main.events_worker import EventRedactBehaviour
from synapse.types import JsonDict
from whoosh.qparser import MultifieldParser

from .message_decryptor import MessageDecryptor
from .message_query import MessageQuery
from .search_index import SearchIndex


class EncryptedSearchStore:
    """
    A handler for searching encrypted messages.

    Overview:
    1. Query the database for encrypted messages in the room.
    2. Decrypt the messages.
    3. Create an in-memory search index.
    4. Perform the search, and return the results.
    """

    def __init__(self, wrapped, key):
        self._wrapped = wrapped
        self.message_decryptor = MessageDecryptor(key)

    async def search_msgs(
        self, room_ids: Collection[str], search_term: str, keys: Iterable[str]
    ) -> JsonDict:
        """Performs a full text search over events with given keys.

        This is used when searching by rank.

        Args:
            room_ids: List of room ids to search in
            search_term: Search term to search for
            keys: List of keys to search in, currently supports "content.body", "content.name", "content.topic"

        Returns:
            Dictionary of results
        """
        return await self._search_in_index(room_ids, search_term, keys)

    async def search_rooms(
        self,
        room_ids: Collection[str],
        search_term: str,
        keys: Iterable[str],
        limit: int,
        pagination_token: Optional[str] = None,
    ) -> JsonDict:
        """Performs a full text search over events with given keys.

        This is used when searching by recency.

        Args:
            room_ids: The room_ids to search in
            search_term: Search term to search for
            keys: List of keys to search in, currently supports "content.body", "content.name", "content.topic"
            limit: The maximum number of results to return
            pagination_token: A pagination token previously returned

        Returns:
            Each match as a dictionary.
        """
        # TODO: Actually respect the limit parameter. Right now we can't because we don't sort in the search, only after.
        return await self._search_in_index(
            room_ids, search_term, keys, True, pagination_token
        )

    async def _search_in_index(
        self,
        room_ids: Collection[str],
        search_term: str,
        keys: Iterable[str],
        recency: bool = False,
        pagination_token: Optional[str] = None,
    ) -> JsonDict:
        encrypted_messages = await MessageQuery.get_encrypted_messages(
            self._wrapped.db_pool, list(room_ids), pagination_token
        )
        decrypted_messages = self.message_decryptor.decrypt(encrypted_messages)
        search_index = SearchIndex.create(decrypted_messages)

        qp = MultifieldParser(
            [key.replace("content.", "") for key in keys], schema=search_index.schema
        )
        q = qp.parse(search_term)

        with search_index.searcher() as s:
            if recency:
                results = s.search(
                    q,
                    limit=None,
                    sortedby=["origin_server_ts", "stream_ordering"],
                    reverse=True,
                )
            else:
                results = s.search(q, limit=None)

            # We set redact_behaviour to block here to prevent redacted events being returned in
            # search results (which is a data leak)
            events = await self.get_events_as_list(  # type: ignore[attr-defined]
                [r["event_id"] for r in results],
                redact_behaviour=EventRedactBehaviour.block,
            )

            event_map = {
                # TODO: Eliminate this extra pass at decrypting events
                ev.event_id: self.message_decryptor.decrypt_event(ev)
                for ev in events
            }

            return {
                "results": [
                    {
                        "event": event_map[r["event_id"]],
                        "rank": float(f"{r.score[0]}.{r.score[1]}")
                        if isinstance(r.score, tuple)
                        else r.score,
                        "pagination_token": "%s,%s"
                        % (r["origin_server_ts"], r["stream_ordering"]),
                    }
                    for r in results
                    if r["event_id"] in event_map
                ],
                "highlights": [],  # TODO: Does not yet support highlighting
                "count": len(events),
            }

    def __getattr__(self, name):
        return getattr(self._wrapped, name)
