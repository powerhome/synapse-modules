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

        # Promote each message to its canonical (original) identity. A message
        # that's itself the winning m.replace edit (see MessageQuery's
        # canonical-id ranking) gets its current body/formatted_body promoted
        # up from m.new_content and is re-keyed under the original's event id
        # — so an edited message is only ever one search candidate, showing
        # current content. The winning row's whole content (including
        # `m.relates_to`/`m.new_content`) is kept, not just body/formatted_body,
        # and carried through to the final re-fetched event below — that's what
        # lets `is_edited` stay derivable downstream from the same relation
        # check clients already do for local search, even though the final
        # event is re-fetched by the original's id (which wouldn't otherwise
        # carry the winning edit's relation on its own).
        current_content_by_canonical_id: dict[str, JsonDict] = {}
        for message in decrypted_messages:
            content = message["event_json"].get("content", {})
            canonical_event_id = message["canonical_event_id"]

            new_content = content.get("m.new_content")
            if new_content:
                content["body"] = new_content.get("body", content.get("body", ""))
                if "formatted_body" in new_content:
                    content["formatted_body"] = new_content["formatted_body"]
                elif "formatted_body" in content:
                    del content["formatted_body"]

            message["event_id"] = canonical_event_id
            current_content_by_canonical_id[canonical_event_id] = content

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

            event_map = {}
            for ev in events:
                # `ev.event_id` here is the canonical (original) id — this
                # re-fetch bypassed the ranking/substitution done above, so
                # its own content may be stale if a later edit won. Replace it
                # wholesale with the already-computed winning content (not
                # just body/formatted_body) so `content.relates_to`/
                # `m.new_content` come along too — that's what makes
                # `is_edited` derivable client-side from the returned event's
                # own `content.relates_to`, the same check used for local
                # search. When the winner was the replacement itself, this
                # does leave `content.relates_to.event_id` equal to this
                # event's own (relabeled) id — looks self-referential, but
                # harmless: nothing downstream depends on that value, only on
                # the relation's presence/variant. This also makes decrypting
                # `ev`'s own (about-to-be-discarded) content unnecessary in the
                # normal case — only fall back to it if a hit has no matching
                # substituted content for some reason.
                current = current_content_by_canonical_id.get(ev.event_id)
                if current is not None:
                    ev.content = dict(current)
                else:
                    # TODO: Eliminate this extra pass at decrypting events
                    ev = self.message_decryptor.decrypt_event(ev)

                event_map[ev.event_id] = ev

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
