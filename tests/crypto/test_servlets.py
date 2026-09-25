"""Tests for the Connect*RestServlet decrypt wrappers in connect/crypto/__init__.py.

These wrappers (ConnectRoomMessageListRestServlet, ConnectSyncRestServlet,
dispatch_push, push_bulk, etc.) call ConnectCryptographer.decrypt_event/
decrypt_content with no try/except around them, so a decryption failure
currently propagates and blows up the request instead of silently handing
back a 200 response with ciphertext still in it.

That's a different, valuable property from "does the field get decrypted at
all" (covered by connect/tests/crypto/test_util.py): it's specifically
guarding against a *future* change — e.g. someone wrapping these calls in a
try/except to fix the resulting 500s — accidentally making the failure mode
fail-open (return the event anyway, still encrypted) instead of fail-closed,
which is exactly the bug already found in
connect/search/message_decryptor.py's decrypt_event.
"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from cryptography.fernet import Fernet, InvalidToken
from synapse.rest.client.room import RoomMessageListRestServlet
from synapse.rest.client.sync import SyncRestServlet

import connect.crypto as crypto_module
from connect.crypto import (
    ConnectRoomMessageListRestServlet,
    ConnectSyncRestServlet,
    dispatch_push,
    push_bulk,
)
from connect.crypto.util import ConnectCryptographer

KEY = Fernet.generate_key()
OTHER_KEY = Fernet.generate_key()


def encrypted_message_event(event_id: str, key: bytes = KEY) -> dict:
    ciphertext = Fernet(key).encrypt(b"hello world").decode()
    return {
        "event_id": event_id,
        "type": "m.room.message",
        "content": {"msgtype": "m.text", "body": ciphertext, "encrypted": True},
    }


def edited_message_event(event_id: str, edit_event_id: str, key: bytes = KEY) -> dict:
    # An original event carrying a bundled `m.replace` edit, both
    # Fernet-encrypted. Mirrors what Synapse actually returns from
    # `/messages` (and `/sync`, `/context`, `/initialSync`) for a message
    # that's been edited: the edit's full event is bundled into the
    # original's `unsigned.m.relations.m.replace` on every serialization
    # (MSC3925), rather than delivered as a lone standalone event a client
    # must separately fetch and reconcile.
    return {
        "event_id": event_id,
        "type": "m.room.message",
        "content": {
            "msgtype": "m.text",
            "body": Fernet(key).encrypt(b"original text").decode(),
            "encrypted": True,
        },
        "unsigned": {
            "m.relations": {
                "m.replace": {
                    "event_id": edit_event_id,
                    "type": "m.room.message",
                    "content": {
                        "msgtype": "m.text",
                        "body": Fernet(key).encrypt(b"* edited text").decode(),
                        "encrypted": True,
                        "m.new_content": {
                            "msgtype": "m.text",
                            "body": Fernet(key).encrypt(b"edited text").decode(),
                            "encrypted": True,
                        },
                    },
                }
            }
        },
    }


class ConnectServletDecryptFailureTestSuite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        crypto_module.cc = ConnectCryptographer(KEY)
        self.hs = Mock()

    async def test_message_list_servlet_does_not_return_undecryptable_event(self):
        bad_event = encrypted_message_event("$a", key=OTHER_KEY)
        servlet = ConnectRoomMessageListRestServlet(self.hs)

        with patch.object(
            RoomMessageListRestServlet,
            "on_GET",
            new=AsyncMock(return_value=(200, {"chunk": [bad_event]})),
        ):
            # Not fail-open: the request errors out rather than returning a
            # 200 whose chunk still contains the raw Fernet ciphertext.
            with self.assertRaises(InvalidToken):
                await servlet.on_GET(Mock(), "!room:localhost")

    async def test_sync_servlet_does_not_return_undecryptable_event_in_join(self):
        bad_event = encrypted_message_event("$a", key=OTHER_KEY)
        response = {
            "rooms": {
                "join": {"!room:localhost": {"timeline": {"events": [bad_event]}}},
                "leave": {},
            }
        }
        servlet = ConnectSyncRestServlet(self.hs)

        with patch.object(
            SyncRestServlet, "on_GET", new=AsyncMock(return_value=(200, response))
        ):
            with self.assertRaises(InvalidToken):
                await servlet.on_GET(Mock())

    async def test_sync_servlet_does_not_return_undecryptable_event_in_leave(self):
        bad_event = encrypted_message_event("$a", key=OTHER_KEY)
        response = {
            "rooms": {
                "join": {},
                "leave": {"!room:localhost": {"timeline": {"events": [bad_event]}}},
            }
        }
        servlet = ConnectSyncRestServlet(self.hs)

        with patch.object(
            SyncRestServlet, "on_GET", new=AsyncMock(return_value=(200, response))
        ):
            with self.assertRaises(InvalidToken):
                await servlet.on_GET(Mock())

    async def test_message_list_servlet_decrypts_bundled_edit(self):
        # Not a corrupted/mismatched-key event this time — a perfectly
        # decryptable one, to isolate the bundled-edit path from the
        # fail-closed behavior covered above. The servlet succeeds (200) and
        # decrypts both the original event's own content and the bundled
        # edit nested in `unsigned.m.relations.m.replace` — which a real
        # client reads for an edited message's displayed text. This is what
        # a client backfilling via `/messages` actually receives for any
        # previously-edited message.
        event = edited_message_event("$original", "$edit")
        servlet = ConnectRoomMessageListRestServlet(self.hs)

        with patch.object(
            RoomMessageListRestServlet,
            "on_GET",
            new=AsyncMock(return_value=(200, {"chunk": [event]})),
        ):
            status, response = await servlet.on_GET(Mock(), "!room:localhost")

        self.assertEqual(status, 200)
        returned_event = response["chunk"][0]

        # The original event's own top-level content decrypted correctly.
        self.assertEqual(returned_event["content"]["body"], "original text")

        # The bundled edit is decrypted too — real plaintext, `encrypted`
        # cleared, exactly as a real client would receive it.
        bundled_edit = returned_event["unsigned"]["m.relations"]["m.replace"]
        self.assertEqual(
            bundled_edit["content"]["m.new_content"]["body"], "edited text"
        )
        self.assertNotIn("encrypted", bundled_edit["content"])

    async def test_dispatch_push_does_not_deliver_undecryptable_event(self):
        bad_event = encrypted_message_event("$a", key=OTHER_KEY)

        with self.assertRaises(InvalidToken):
            await dispatch_push(Mock(), bad_event)

    async def test_push_bulk_does_not_forward_undecryptable_event(self):
        bad_event = Mock()
        bad_event.content = encrypted_message_event("$a", key=OTHER_KEY)["content"]

        with self.assertRaises(InvalidToken):
            await push_bulk(
                Mock(),
                service=Mock(),
                events=[bad_event],
                ephemeral=[],
                to_device_messages=[],
                one_time_keys_count=Mock(),
                unused_fallback_keys=Mock(),
                device_list_summary=Mock(),
            )


if __name__ == "__main__":
    unittest.main()
