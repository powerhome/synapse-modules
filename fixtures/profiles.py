"""Writes the ``connect.profiles`` rows that make a user visible in the directory."""

import json
import logging

from synapse.types import JsonDict

logger = logging.getLogger(__name__)

# The SCIM extension the populator's profile ``data`` nests the org attributes
# under; ``connect/profiles/store.py`` reads department/territory from here.
SCIM_EXTENSION = "urn:ietf:params:scim:schemas:extension:authservice:2.0:User"

PROFILES_TABLE = "connect.profiles"


class ProfileWriter:
    """Seeds, deactivates and removes a user's ``connect.profiles`` row.

    ``/_connect/profiles`` and ``/_connect/directory`` inner-join Synapse's
    profiles to ``connect.profiles``, so a user with no row there is invisible to
    the people directory (a profile lookup is a 404). In production the populator
    writes the row from the SCIM user; the fixtures module writes the same shape
    from a user spec's ``profile`` block, so a seeded user has a job title,
    department and territory like a real one.
    """

    def __init__(self, hs):
        self.db_pool = hs.get_datastores().main.db_pool

    async def write(
        self, localpart: str, profile: JsonDict, external_id: str | None
    ) -> None:
        """Upsert an active profile row for ``localpart``.

        Args:
            localpart (str): The user's Matrix localpart (the table's key).
            profile (JsonDict): The spec's ``profile`` block — any of ``job_title``, ``department``, ``territory`` and ``territory_abbreviation``. Unset fields are left out of the row, as the directory queries treat them as null.
            external_id (str | None): The user's linked SCIM external id, if any.
        """
        await self.db_pool.simple_upsert(
            table=PROFILES_TABLE,
            keyvalues={"user_id": localpart},
            values={
                "active": True,
                "data": json.dumps(self._data(profile, external_id)),
            },
            desc="fixtures_write_profile",
        )
        logger.info("fixtures wrote connect profile for %s", localpart)

    async def deactivate(self, localpart: str) -> None:
        """Mark ``localpart``'s profile inactive, as the populator does for a departed user.

        A user with no profile row is left alone.

        Args:
            localpart (str): The user's Matrix localpart.
        """
        await self.db_pool.simple_update(
            table=PROFILES_TABLE,
            keyvalues={"user_id": localpart},
            updatevalues={"active": False},
            desc="fixtures_deactivate_profile",
        )

    async def remove(self, localparts: list[str]) -> None:
        """Delete the profile rows of torn-down users.

        Args:
            localparts (list[str]): The Matrix localparts whose rows to delete.
        """
        for localpart in localparts:
            await self.db_pool.simple_delete(
                table=PROFILES_TABLE,
                keyvalues={"user_id": localpart},
                desc="fixtures_remove_profile",
            )

    @staticmethod
    def _data(profile: JsonDict, external_id: str | None) -> JsonDict:
        data: JsonDict = {}
        if profile.get("job_title"):
            data["title"] = profile["job_title"]
        extension = {
            scim_key: profile[spec_key]
            for spec_key, scim_key in (
                ("department", "department"),
                ("territory", "territory"),
                ("territory_abbreviation", "territoryAbbr"),
            )
            if profile.get(spec_key)
        }
        if extension:
            data[SCIM_EXTENSION] = extension
        if external_id:
            data["externalId"] = external_id
        return data
