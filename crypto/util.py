"""Utilities for encryption at rest."""

import io

from cryptography.fernet import Fernet
from synapse.http.site import SynapseRequest
from synapse.module_api import parse_json_object_from_request
from synapse.types import JsonDict
from synapse.util import json_encoder


class ConnectCryptographer:
    """A class that encrypts and decrypts messages in-place."""

    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    def encrypt_request(self, request: SynapseRequest, event_type: str) -> None:
        if event_type == "m.room.message":
            content = parse_json_object_from_request(request)
            self._encrypt_content(content)
            content_as_string = json_encoder.encode(content)
            content_as_bytes = content_as_string.encode()
            request.content = io.BytesIO(content_as_bytes)

    def _encrypt_content(self, content: JsonDict) -> None:
        if content.get("msgtype") == "m.text":
            self._encrypt_body(content)
            self._encrypt_formatted_body(content)

            if "m.new_content" in content:
                self._encrypt_body(content["m.new_content"])
                self._encrypt_formatted_body(content["m.new_content"])

            content["encrypted"] = True

    def _encrypt_body(self, content: JsonDict) -> None:
        if "body" in content:
            content["body"] = self._fernet.encrypt(content["body"].encode()).decode()

    def _encrypt_formatted_body(self, content: JsonDict) -> None:
        if "formatted_body" in content:
            content["formatted_body"] = self._fernet.encrypt(
                content["formatted_body"].encode()
            ).decode()

    def decrypt_event(self, event: JsonDict) -> None:
        if event.get("type") == "m.room.message" and "content" in event:
            self.decrypt_content(event["content"])
            self._decrypt_bundled_replace(event)

    def _decrypt_bundled_replace(self, event: JsonDict) -> None:
        # Synapse injects the latest edit's full event into
        # unsigned.m.relations.m.replace on every serialization (MSC3925
        # bundled aggregations; see synapse/events/utils.py's
        # _inject_bundled_aggregations). Clients read an edited message's
        # displayed text from this bundled copy rather than a separately
        # synced edit event, so it needs the same decryption as the event's
        # own top-level content.
        #
        # NOTE: unsigned.m.relations.m.thread has an analogous gap (a full
        # event nested at .latest_event.content) but nothing in connect/
        # currently requests or reads thread-summary bundling, so it's
        # intentionally left unhandled here.
        replace = event.get("unsigned", {}).get("m.relations", {}).get("m.replace")
        if isinstance(replace, dict) and "content" in replace:
            self.decrypt_content(replace["content"])

    def decrypt_content(self, content: JsonDict) -> None:
        if content.get("encrypted") is not True:
            return

        if content.get("msgtype") == "m.text":
            self._decrypt_body(content)
            self._decrypt_formatted_body(content)

            if "m.new_content" in content:
                self._decrypt_body(content["m.new_content"])
                self._decrypt_formatted_body(content["m.new_content"])

            del content["encrypted"]

    def _decrypt_body(self, content: JsonDict) -> None:
        if "body" in content:
            content["body"] = self._fernet.decrypt(content["body"].encode()).decode()

    def _decrypt_formatted_body(self, content: JsonDict) -> None:
        if "formatted_body" in content:
            content["formatted_body"] = self._fernet.decrypt(
                content["formatted_body"].encode()
            ).decode()
