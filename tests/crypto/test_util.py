"""Tests for ConnectCryptographer.

This is the decrypt path used by every live-traffic servlet wrapper in
connect/crypto/__init__.py (send, /messages, /event/{id}, /context/{id},
initial sync, /sync, sliding sync, push dispatch, and appservice push_bulk).
Unlike connect/search/message_decryptor.py's decrypt_event, this code has no
try/except around Fernet decryption at all, so these tests document two
different ways ciphertext can still reach a client through this path:

1. An anomalous event where `content.encrypted` is True but `msgtype` isn't
   "m.text" — decrypt_content silently no-ops on the body/formatted_body and
   never clears the `encrypted` flag, with no exception and nothing logged.
   The event is returned to the client exactly as read from the DB, still
   ciphertext.
2. A Fernet decryption failure (wrong/rotated key, corrupted ciphertext) —
   unlike message_decryptor.py's fail-open behavior, this path has no
   try/except at all, so the exception propagates uncaught. That's an
   availability problem (the request presumably 500s), not a silent leak,
   but it's worth pinning down explicitly since a future change that adds a
   blanket try/except here (to fix the availability problem) could easily
   reintroduce the same fail-open leak already found in message_decryptor.py
   if it isn't careful to drop rather than pass through on failure.
"""

import unittest

from cryptography.fernet import Fernet, InvalidToken

from connect.crypto.util import ConnectCryptographer

KEY = Fernet.generate_key()
OTHER_KEY = Fernet.generate_key()


class ConnectCryptographerTestSuite(unittest.TestCase):
    def setUp(self):
        self.cc = ConnectCryptographer(KEY)

    def _encrypt(self, plaintext: str, key: bytes = KEY) -> str:
        return Fernet(key).encrypt(plaintext.encode()).decode()

    def test_decrypt_content_decrypts_valid_text_message(self):
        content = {
            "msgtype": "m.text",
            "body": self._encrypt("hello world"),
            "encrypted": True,
        }

        self.cc.decrypt_content(content)

        self.assertEqual(content["body"], "hello world")
        self.assertNotIn("encrypted", content)

    def test_decrypt_content_decrypts_edits_in_new_content(self):
        content = {
            "msgtype": "m.text",
            "body": self._encrypt("* edited"),
            "encrypted": True,
            "m.new_content": {
                "msgtype": "m.text",
                "body": self._encrypt("edited"),
            },
        }

        self.cc.decrypt_content(content)

        self.assertEqual(content["m.new_content"]["body"], "edited")

    def test_decrypt_content_leaks_ciphertext_when_msgtype_is_not_text(self):
        # Anomalous state: encrypted=True is only ever set alongside
        # msgtype == "m.text" by _encrypt_content, but if content ever
        # reaches here with a different msgtype (e.g. a bug elsewhere, or a
        # future msgtype that also sets `encrypted`), decrypt_content's
        # `msgtype == "m.text"` guard means the body is never touched.
        ciphertext = self._encrypt("hello world")
        content = {
            "msgtype": "m.image",
            "body": ciphertext,
            "encrypted": True,
        }

        self.cc.decrypt_content(content)

        # BUG: silent leak. No exception, nothing logged, and the client
        # receives the raw Fernet token in `body` with `encrypted` still True.
        self.assertEqual(content["body"], ciphertext)
        self.assertTrue(content["encrypted"])

    def test_decrypt_content_raises_instead_of_leaking_on_bad_ciphertext(self):
        # Encrypted with a key this decryptor doesn't hold — simulates key
        # rotation or a corrupted ciphertext.
        content = {
            "msgtype": "m.text",
            "body": self._encrypt("hello world", key=OTHER_KEY),
            "encrypted": True,
        }

        # Unlike message_decryptor.MessageDecryptor.decrypt_event, there is no
        # try/except here: a bad token blows up the request rather than
        # silently returning ciphertext. Good for confidentiality, bad for
        # availability — pinning this down so a future "let's add error
        # handling here" change doesn't accidentally make it fail-open
        # instead of fail-closed.
        with self.assertRaises(InvalidToken):
            self.cc.decrypt_content(content)

        # And critically, if a caller ignored/swallowed that exception, the
        # content dict passed in would still be mutated in this
        # half-decrypted state: `encrypted` never got cleared, `body` is
        # still ciphertext.
        self.assertTrue(content["encrypted"])
        self.assertNotEqual(content["body"], "hello world")

    def test_decrypt_event_ignores_non_message_events(self):
        event = {"type": "m.reaction", "content": {"encrypted": True}}

        self.cc.decrypt_event(event)

        # Not a bug: reactions/other event types are never encrypted by
        # _encrypt_content in the first place, so this is a correct no-op —
        # included here so a future change to widen encryption coverage to
        # other event types doesn't silently forget to widen decryption too.
        self.assertTrue(event["content"]["encrypted"])

    def test_decrypt_event_noop_when_not_marked_encrypted(self):
        content = {"msgtype": "m.text", "body": "already plaintext"}
        event = {"type": "m.room.message", "content": content}

        self.cc.decrypt_event(event)

        self.assertEqual(content["body"], "already plaintext")


if __name__ == "__main__":
    unittest.main()
