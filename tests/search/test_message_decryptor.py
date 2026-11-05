"""Tests for MessageDecryptor.

These tests document current behavior, including a fail-open bug: when
`decrypt_event` cannot decrypt an event's content (e.g. a key rotation or a
corrupted ciphertext), it returns the event unchanged rather than dropping
it. That event still carries raw Fernet ciphertext in
`content.body`/`content.formatted_body` and `content.encrypted == True`, and
is what `EncryptedSearchStore._search_in_index` returns to the client search
endpoint when a hit isn't covered by the pre-decrypted canonical content map.
"""

import unittest
from unittest.mock import Mock

from cryptography.fernet import Fernet

from connect.search.message_decryptor import MessageDecryptor, logger

KEY = Fernet.generate_key()
OTHER_KEY = Fernet.generate_key()


def encrypted_message(event_id: str, body: str, key: bytes = KEY) -> dict:
    fernet = Fernet(key)
    return {
        "event_id": event_id,
        "event_json": {
            "content": {
                "msgtype": "m.text",
                "body": fernet.encrypt(body.encode()).decode(),
                "encrypted": True,
            }
        },
    }


def mock_event(event_id: str, content: dict):
    event = Mock()
    event.event_id = event_id
    event.content = content
    return event


class MessageDecryptorTestSuite(unittest.TestCase):
    def setUp(self):
        self.decryptor = MessageDecryptor(KEY)

    def test_decrypt_decrypts_valid_messages(self):
        message = encrypted_message("$a", "hello world")

        [decrypted] = self.decryptor.decrypt([message])

        content = decrypted["event_json"]["content"]
        self.assertEqual(content["body"], "hello world")
        self.assertFalse(content["encrypted"])

    def test_decrypt_drops_messages_that_fail_to_decrypt(self):
        # Encrypted with a key the decryptor doesn't hold — simulates key
        # rotation or a corrupted ciphertext.
        message = encrypted_message("$a", "hello world", key=OTHER_KEY)

        decrypted = self.decryptor.decrypt([message])

        # Fail-closed: the undecryptable message is dropped entirely, never
        # reaching a client with its ciphertext intact.
        self.assertEqual(decrypted, [])

    def test_decrypt_event_decrypts_valid_event(self):
        ciphertext = Fernet(KEY).encrypt(b"hello world").decode()
        event = mock_event(
            "$a",
            {"msgtype": "m.text", "body": ciphertext, "encrypted": True},
        )

        result = self.decryptor.decrypt_event(event)

        self.assertEqual(result.content["body"], "hello world")
        self.assertFalse(result.content["encrypted"])

    def test_decrypt_event_leaks_ciphertext_when_decryption_fails(self):
        # Encrypted with a key the decryptor doesn't hold — simulates key
        # rotation or a corrupted ciphertext, the same scenario that's
        # fail-closed in decrypt()/_decrypt_message() above.
        ciphertext = Fernet(OTHER_KEY).encrypt(b"hello world").decode()
        event = mock_event(
            "$a",
            {"msgtype": "m.text", "body": ciphertext, "encrypted": True},
        )

        result = self.decryptor.decrypt_event(event)

        # BUG: fail-open. The event is handed back unchanged — still
        # carrying raw Fernet ciphertext in `body` and `encrypted == True` —
        # instead of being dropped like the equivalent case in decrypt().
        self.assertIs(result, event)
        self.assertEqual(result.content["body"], ciphertext)
        self.assertTrue(result.content["encrypted"])

    def test_decrypt_event_warns_when_returning_ciphertext(self):
        ciphertext = Fernet(OTHER_KEY).encrypt(b"hello world").decode()
        event = mock_event(
            "$a",
            {"msgtype": "m.text", "body": ciphertext, "encrypted": True},
        )

        with self.assertLogs(logger, level="WARNING") as logs:
            self.decryptor.decrypt_event(event)

        self.assertTrue(
            any("$a" in message and "ciphertext" in message for message in logs.output),
            f"expected a warning naming event $a and ciphertext, got: {logs.output}",
        )


if __name__ == "__main__":
    unittest.main()
