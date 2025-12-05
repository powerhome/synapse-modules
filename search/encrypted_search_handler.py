"""
ConnectSearchRestServlet module

Message searching including decryption of encrypted messages
and integration with an in-memory search index.
"""

from typing import TYPE_CHECKING

from synapse.handlers.search import SearchHandler

from .encrypted_search_store import EncryptedSearchStore

if TYPE_CHECKING:
    from synapse.server import HomeServer


class EncryptedSearchHandler(SearchHandler):
    """A handler for searching encrypted messages."""

    def __init__(self, hs: "HomeServer", key: bytes):
        """
        Initialize the EncryptedSearchHandler.

        Args:
            hs (HomeServer): The HomeServer instance.
            key (bytes): The encryption key used for decrypting messages.
        """
        super().__init__(hs)

        self.store = EncryptedSearchStore(hs.get_datastores().main, key)
