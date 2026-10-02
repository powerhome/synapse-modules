"""This module provides the SearchIndex class for indexing and searching decrypted messages."""

from whoosh.analysis import StemmingAnalyzer
from whoosh.fields import ID, NUMERIC, TEXT, Schema
from whoosh.filedb.filestore import RamStorage


class SearchIndex:
    """This class creates an in-memory search index of decrypted messages."""

    @classmethod
    def create(cls, decrypted_messages: list[dict[str, any]]):
        schema = Schema(
            event_id=ID(stored=True, unique=True),
            room_id=ID(stored=True),
            sender=ID(stored=True),
            body=TEXT(analyzer=StemmingAnalyzer(), stored=True, vector=True),
            origin_server_ts=NUMERIC(int, 64, stored=True, sortable=True),
            stream_ordering=NUMERIC(stored=True, sortable=True),
        )

        storage = RamStorage()
        index = storage.create_index(schema)

        writer = index.writer()
        for event in decrypted_messages:
            content = event["event_json"].get("content", {})
            body = content.get("body", "")

            writer.add_document(
                event_id=event["event_id"],
                room_id=event["room_id"],
                sender=event["event_json"].get("sender", ""),
                body=body,
                origin_server_ts=str(event["origin_server_ts"]),
                stream_ordering=str(event["stream_ordering"]),
            )

        writer.commit()
        return index
