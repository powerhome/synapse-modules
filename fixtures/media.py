"""Provisions media uploads (stub)."""

from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict


class MediaProvisioner:
    """Media uploads to the media-repo endpoint.

    Until this is implemented, asking to provision media reports
    not-yet-implemented.
    """

    def __init__(self, hs):
        self.hs = hs

    async def provision(self, spec: JsonDict, users: dict[str, JsonDict]) -> JsonDict:
        # TODO: upload the named fixture as the uploading user via the media-repo
        #       endpoint; return its MXC URI for an event to attach.
        raise SynapseError(
            HTTPStatus.NOT_IMPLEMENTED,
            "media provisioning is not yet implemented",
            Codes.UNKNOWN,
        )
