"""Enriches a fixture user with audiences directory state.

A user's directory presence (its audiences ``ExternalUser`` and group
memberships) lives in the audiences Rails service, not in Synapse. When a user
spec carries a ``directory`` block, this enricher upserts that state through the
audiences internal API — the same in-cluster, unauthenticated ``audiences/api/*``
family the rest of the platform uses — so the Matrix account the
``UserProvisioner`` then registers is a fully org-modeled user, exactly as the
populator produced. This is the populator's two halves (audiences seed + Matrix
registration) unified behind one declarative call.
"""

import logging

from synapse.types import JsonDict

logger = logging.getLogger(__name__)


class DirectoryEnricher:
    """Upserts an audiences ``ExternalUser`` + groups for a fixture user."""

    def __init__(self, hs, base_url: str):
        self.client = hs.get_simple_http_client()
        self._endpoint = f"{base_url.rstrip('/')}/audiences/api/external_users"

    async def enrich(self, spec: JsonDict, localpart: str, directory: JsonDict) -> None:
        # Upsert the user's audiences directory entry. ``directory`` carries
        # ``groups`` ("ResourceType:value" shorthand) and an optional
        # ``external_id`` (defaults to the localpart); the localpart is the SCIM
        # ``userName`` and ``display_name`` falls back to it.
        external_id = directory.get("external_id") or localpart
        body = {
            "scim_id": external_id,
            "external_id": external_id,
            "user_name": localpart,
            "display_name": spec.get("display_name") or localpart,
            "active": True,
            "groups": [self._parse_group(g) for g in directory.get("groups", [])],
        }

        logger.info("fixtures enriching directory for %s", localpart)
        await self.client.put_json(self._endpoint, body)

    @staticmethod
    def _parse_group(group: str) -> JsonDict:
        # Split a "ResourceType:value" shorthand into a group descriptor. The
        # value doubles as the group's external_id/scim_id and display name;
        # audiences find-or-creates the group on upsert.
        resource_type, _, value = group.partition(":")
        return {
            "resource_type": resource_type,
            "value": value or resource_type,
            "display": value or resource_type,
        }
