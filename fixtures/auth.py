"""Shared-secret authentication for fixtures requests.

One bearer-token check shared by every fixtures endpoint — the main-process
scenario/cleanup servlets and the receipts-writer receipt servlet — so they all
reject an unauthenticated request identically.
"""

import hmac
from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.http.site import SynapseRequest


def authenticate_fixtures_request(request: SynapseRequest, secret: str) -> None:
    """Reject ``request`` unless it carries the fixtures shared secret.

    Args:
        request (SynapseRequest): The incoming request.
        secret (str): The fixtures shared secret the request must present.

    Raises:
        SynapseError: 403 if the bearer token is missing or wrong.
    """
    header = request.getHeader("Authorization") or ""
    prefix = "Bearer "
    provided = header[len(prefix) :] if header.startswith(prefix) else ""
    if not provided or not hmac.compare_digest(provided, secret):
        raise SynapseError(
            HTTPStatus.FORBIDDEN,
            "invalid or missing fixtures secret",
            Codes.FORBIDDEN,
        )
