"""Pins a known Fernet-decryption coverage gap.

connect/servlets/__init__.py only re-registers a specific allowlist of
Connect*Servlet wrappers (see room_register_servlets,
room_register_deprecated_servlets, sync_register_servlets in that module) to
decrypt m.room.message content before it's returned to a client. Any Synapse
endpoint that can serve event content but isn't in that allowlist keeps
Synapse's original, non-decrypting behavior — so a client hitting it
receives raw Fernet ciphertext in `content.body`/`content.formatted_body`,
with no failure, exception, or log line at all.

Known endpoints in this gap today (confirmed by reading
synapse/rest/__init__.py's CLIENT_SERVLET_FUNCTIONS and comparing against
connect/servlets/__init__.py's wrapped subset):

- GET /rooms/{roomId}/relations/... (RelationPaginationServlet)
- GET /_matrix/client/v1/rooms/{roomId}/threads (ThreadsServlet)
- GET /_synapse/admin/v1/rooms/{roomId}/messages (admin RoomMessagesRestServlet)

If this test starts failing because one of these got a Connect wrapper,
that's good news: update the `known_unwrapped` set (and this docstring) to
drop it.
"""

import inspect
import unittest

import connect.crypto as crypto_module
import connect.servlets as servlets_module

KNOWN_UNWRAPPED_LEAK_SURFACES = {
    "RelationPaginationServlet",
    "ThreadsServlet",
    "RoomMessagesRestServlet",
}


class ConnectDecryptCoverageGapTestSuite(unittest.TestCase):
    def test_known_unwrapped_endpoints_still_have_no_connect_decrypt_wrapper(self):
        crypto_source = inspect.getsource(crypto_module)
        servlets_source = inspect.getsource(servlets_module)

        for name in KNOWN_UNWRAPPED_LEAK_SURFACES:
            wrapper_name = f"Connect{name}"
            self.assertNotIn(
                wrapper_name,
                crypto_source,
                f"{wrapper_name} now exists in connect/crypto/__init__.py, "
                f"suggesting {name} is decrypted — if it's also registered "
                "in connect/servlets/__init__.py, this coverage gap is "
                "closed; update this test.",
            )
            self.assertNotIn(
                wrapper_name,
                servlets_source,
                f"{wrapper_name} is now registered in "
                "connect/servlets/__init__.py — this coverage gap may be "
                "closed; update this test.",
            )


if __name__ == "__main__":
    unittest.main()
