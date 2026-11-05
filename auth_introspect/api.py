"""Auth introspect HTTP resource.

`GET /_connect/auth/introspect` validates the request's access token the
normal Synapse way and answers in a shape nginx `auth_request` can use, since
`auth_request` only sees the status code and headers, never the body:

  * `204` with the user's localpart in `X-Connect-Localpart` for a valid,
    non-guest user;
  * `401` for anything else (missing, invalid, expired or logged-out token,
    guest, locked or expired account).

Neither response has a body. The header only travels from Synapse to the
presence sidecar, which moves the localpart into the upstream path and drops
the header, so it never reaches clients or the presence service.

The endpoint is internal-only; see `nginx.conf.template`.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

from synapse.api.errors import AuthError, InvalidClientCredentialsError
from synapse.http.server import DirectServeJsonResource, finish_request
from synapse.http.site import SynapseRequest

if TYPE_CHECKING:
    from synapse.server import HomeServer

INTROSPECT_PATH = "/_connect/auth/introspect"
LOCALPART_HEADER = b"X-Connect-Localpart"


class IntrospectResource(DirectServeJsonResource):
    """Answers `auth_request` subrequests from the presence sidecar."""

    def __init__(self, hs: "HomeServer"):
        super().__init__(clock=hs.get_clock())
        self._auth = hs.get_auth()

    async def _async_render_GET(self, request: SynapseRequest) -> None:
        """Handle ``GET /_connect/auth/introspect``.

        Unexpected errors (e.g. the database being down) propagate to
        Synapse's normal 500 handling, which `auth_request` also rejects, so
        the request fails closed.

        Args:
            request: Incoming Synapse request object.
        """
        try:
            # Guests are rejected by the default `allow_guest=False`, as are
            # expired tokens (`allow_expired=False`) and locked users.
            requester = await self._auth.get_user_by_req(request)
        except (AuthError, InvalidClientCredentialsError):
            # Guests raise a 403; `auth_request` only passes through 401 and
            # 403, and every rejection here means the same thing to the
            # sidecar, so answer 401 uniformly and without a JSON body.
            _respond(request, HTTPStatus.UNAUTHORIZED)
            return

        request.setHeader(LOCALPART_HEADER, requester.user.localpart.encode())
        _respond(request, HTTPStatus.NO_CONTENT)


def _respond(request: SynapseRequest, code: HTTPStatus) -> None:
    """Finish the request with `code` and an empty body.

    Args:
        request: Request to finish.
        code: HTTP status code to send.
    """
    request.setResponseCode(code)
    finish_request(request)
