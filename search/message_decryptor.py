"""This module provides the MessageDecryptor class for decrypting encrypted messages."""

import logging

from cryptography.fernet import Fernet
from synapse.events import EventBase

logger = logging.getLogger(__name__)


class MessageDecryptor:
    """
    A class to decrypt encrypted messages using the Fernet symmetric encryption method.

    Attributes:
        key (Fernet): The key used for decryption.
    """

    def __init__(self, key: bytes):
        self.key = Fernet(key)

    def decrypt(self, encrypted_messages: list[dict[str, any]]) -> list[dict[str, any]]:
        return [
            decrypted
            for message in encrypted_messages
            if (decrypted := self._decrypt_message(message)) is not None
        ]

    def _decrypt_message(self, message: dict[str, any]) -> None:
        event_json = message["event_json"]
        content = event_json.get("content", {})
        if content.get("encrypted") is not True:
            return message

        encrypted_body = content.get("body")
        if not encrypted_body:
            return None

        try:
            decrypted_body_bytes = self.key.decrypt(encrypted_body.encode("utf-8"))
            decrypted_body = decrypted_body_bytes.decode("utf-8")
            event_json["content"]["body"] = decrypted_body

            encrypted_formatted_body = event_json.get("content", {}).get(
                "formatted_body"
            )
            if encrypted_formatted_body:
                decrypted_formatted_body_bytes = self.key.decrypt(
                    encrypted_formatted_body.encode("utf-8")
                )
                decrypted_formatted_body = decrypted_formatted_body_bytes.decode(
                    "utf-8"
                )
                event_json["content"]["formatted_body"] = decrypted_formatted_body

            event_json["content"]["encrypted"] = False

            message["event_json"] = event_json
            return message
        except Exception as e:
            logger.error(f"Failed to decrypt message {message['event_id']}: {e}")
            return None

    def decrypt_event(self, event: EventBase) -> EventBase:
        content = event.content
        if content.get("encrypted") is not True:
            return event

        encrypted_body = content.get("body")
        if not encrypted_body:
            return event

        try:
            decrypted_body_bytes = self.key.decrypt(encrypted_body.encode("utf-8"))
            decrypted_body = decrypted_body_bytes.decode("utf-8")
            content["body"] = decrypted_body

            encrypted_formatted_body = content.get("formatted_body")
            if encrypted_formatted_body:
                decrypted_formatted_body_bytes = self.key.decrypt(
                    encrypted_formatted_body.encode("utf-8")
                )
                decrypted_formatted_body = decrypted_formatted_body_bytes.decode(
                    "utf-8"
                )
                content["formatted_body"] = decrypted_formatted_body

            content["encrypted"] = False

            event.content = content
            return event
        except Exception as e:
            logger.error(f"Failed to decrypt event {event.event_id}: {e}")
            return event
