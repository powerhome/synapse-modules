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

# CPG's `ConnectAccessResolver::RESOURCE_TYPE`.
ACCESS_GROUP_RESOURCE_TYPE = "Generics"


class DirectoryEnricher:
    """Upserts an audiences ``ExternalUser`` + groups for a fixture user."""

    def __init__(self, hs, base_url: str, access_group_external_id: str = ""):
        self.client = hs.get_simple_http_client()
        self._endpoint = f"{base_url.rstrip('/')}/audiences/api/external_users"
        # The Connect Access Group's external id on a gated tenant (Power), else
        # empty. CPG gives Connect access only to its members and fails closed
        # when the group does not exist, so every directory user joins it.
        self._access_group_external_id = access_group_external_id

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
            "groups": self._groups(directory),
        }

        logger.info("fixtures enriching directory for %s", localpart)
        await self.client.put_json(self._endpoint, body)

    def _groups(self, directory: JsonDict) -> list[JsonDict]:
        groups = [self.parse_group(g) for g in directory.get("groups", [])]
        # `connect_access: false` opts a user out, to model one without Connect
        # access; the default mirrors the dev seeder, where every user has it.
        if self._access_group_external_id and directory.get("connect_access", True):
            access_group = {
                "resource_type": ACCESS_GROUP_RESOURCE_TYPE,
                "value": self._access_group_external_id,
                "display": "Connect Access Group",
            }
            if not any(
                g["resource_type"] == access_group["resource_type"]
                and g["value"] == access_group["value"]
                for g in groups
            ):
                groups.append(access_group)
        return groups

    @staticmethod
    def parse_group(group: str) -> JsonDict:
        # Split a "ResourceType:value" shorthand into a group descriptor. The
        # value doubles as the group's external_id/scim_id and display name;
        # audiences find-or-creates the group on upsert.
        resource_type, _, value = group.partition(":")
        return {
            "resource_type": resource_type,
            "value": value or resource_type,
            "display": value or resource_type,
        }
