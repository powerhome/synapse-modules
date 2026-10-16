"""
ConnectSearchRestServlet module

Message searching including decryption of encrypted messages
and integration with an in-memory search index.
"""

from typing import TYPE_CHECKING

from synapse.rest.client.room import SearchRestServlet

from .encrypted_search_handler import EncryptedSearchHandler

if TYPE_CHECKING:
    from synapse.server import HomeServer


class ConnectSearchRestServlet(SearchRestServlet):
    """
    A servlet for handling search requests.

    Overview:
    1. Query the database for encrypted messages in the room.
    2. Decrypt the messages.
    3. Create an in-memory search index.
    4. Perform the search, and return the results.
    """

    def __init__(self, hs: "HomeServer", key: bytes):
        """
        Initialize the ConnectSearchRestServlet.

        Args:
            hs (HomeServer): The HomeServer instance.
            key (bytes): The encryption key used for decrypting messages.
        """
        super().__init__(hs)

        self.search_handler = EncryptedSearchHandler(hs, key)
