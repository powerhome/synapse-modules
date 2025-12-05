"""Populator module."""

import json
import logging
from typing import TYPE_CHECKING, Any

from synapse.types import JsonDict, Optional, UserID, create_requester
from twisted.internet import defer

from ..audiences.audiences_bot import AudiencesBot
from ..helpers.user import UserHelpers

if TYPE_CHECKING:
    from synapse.server import HomeServer


logger = logging.getLogger(__name__)


class Populator:
    """A class that fetches and stores data for all applicable users."""

    def __init__(self, config: dict[str, Any], hs: "HomeServer"):
        token = config["hs_token"].encode("ascii")
        self.headers = {b"Authorization": [b"Bearer " + token]}

        self.client = hs.get_simple_http_client()
        self.api = hs.get_module_api()
        self.config = config
        self.hs = hs

        self.store = hs.get_datastores().main
        self.db_pool = self.store.db_pool

        self.idp_id = f"oidc-{config['idp_id']}"

    def request_synapse_audience(self):
        defer.ensureDeferred(self._request())

    async def _request(self):
        try:
            body = await self.client.post_json_get_json(
                uri="http://audiences:3000/audiences/api/clients",
                post_json={},
                headers=self.headers,
            )
            assert body["name"] == "synapse"
        except Exception as e:
            # TODO improve retry logic
            logger.warning(f"Could not connect to audiences, retrying: {e}")
            await self.api.sleep(60.0)
            await self._request()

    async def inject_localparts(self, users: list[JsonDict]):
        mappable = dict(
            await self.db_pool.simple_select_list(
                "user_external_ids",
                keyvalues={"auth_provider": self.idp_id},
                retcols=("external_id", "user_id"),
            )
        )

        for user in users:
            external_id = self._external_user_id_for_user(user)
            if external_id in mappable:
                mxid = UserID.from_string(mappable[external_id])
                user["matrix_localpart"] = mxid.localpart
            else:
                user["matrix_localpart"] = f"u{external_id}"

    async def populate_single_user(self, user: JsonDict):
        await self.inject_localparts([user])
        await self.populate([user])
        await self.update_display_names([user])
        await self.register_unregistered_users([user])
        await self.toggle_user_active(user)

    async def populate_all_users(self, users: list[JsonDict]):
        await self.inject_localparts(users)
        await self.populate(users)
        await self.deactivate_users(users)
        await self.update_display_names(users)
        await self.register_unregistered_users(users)
        await self.reactivate_users(users)

    async def populate(self, users: list[JsonDict]):
        logger.info(f"Populating {len(users)} users")

        key_values = [[user["matrix_localpart"]] for user in users]
        value_values = [[user["active"], json.dumps(user)] for user in users]

        await self.db_pool.simple_upsert_many(
            table="connect.profiles",
            key_names=["user_id"],
            key_values=key_values,
            value_names=["active", "data"],
            value_values=value_values,
            desc="",
        )

    async def deactivate_users(self, users: list[JsonDict]):
        current_user_ids = await self.db_pool.simple_select_onecol(
            table="connect.profiles",
            keyvalues={"active": True},
            retcol="user_id",
        )
        active_user_ids = [
            user["matrix_localpart"] for user in users if user.get("active")
        ]
        inactive_user_ids = set(current_user_ids) - set(active_user_ids)

        if not inactive_user_ids:
            return

        logger.info(f"Deactivating {len(inactive_user_ids)} users: {inactive_user_ids}")

        # Update connect.profile active status
        await self.db_pool.runInteraction(
            "deactivate_users_txn", self._deactivate_users_txn, inactive_user_ids
        )

        deactivated_users = await UserHelpers.get_deactivated_users(self.db_pool)
        for user_name in inactive_user_ids:
            mxid = await self._mxid_from_username(user_name)

            # Check if the user is already deactivated in the users table
            if mxid:
                if mxid not in deactivated_users:
                    await self._deactivate_user_by_mxid(mxid)
            else:
                logger.warning(
                    f"Could not find mxid for {user_name}, unable to deactivate"
                )

    def _deactivate_users_txn(self, txn, inactive_user_ids: list[str]):
        key_values = [[user_id] for user_id in inactive_user_ids]
        value_values = [[False] for _ in inactive_user_ids]

        self.db_pool.simple_update_many_txn(
            txn=txn,
            table="connect.profiles",
            key_names=["user_id"],
            key_values=key_values,
            value_names=["active"],
            value_values=value_values,
        )

    async def _deactivate_user_by_mxid(self, mxid: str):
        # Prevent the user from logging in and from being considered in UI.
        logger.info(f"Deactivating user with mxid: {mxid}")
        audiences_bot_mxid = AudiencesBot(self.config, self.hs).bot_mxid
        await self.hs.get_deactivate_account_handler().deactivate_account(
            mxid, erase_data=False, requester=create_requester(audiences_bot_mxid)
        )

    async def update_display_names(self, users: list[JsonDict]):
        display_names = await self.db_pool.simple_select_list(
            table="profiles", keyvalues=None, retcols=("user_id", "displayname")
        )
        new_display_names = {
            user["matrix_localpart"]: user.get("displayName") for user in users
        }

        for user_id, display_name in display_names:
            new_display_name = new_display_names.get(user_id)
            if new_display_name and new_display_name != display_name:
                logger.info(
                    f"Updating {user_id}'s display name from '{display_name}' to '{new_display_name}'"
                )
                await self.api.set_displayname(
                    UserID(user_id, self.hs.hostname), new_display_name
                )

    async def register_unregistered_users(self, users: list[JsonDict]):
        registered_user_subs = set(
            await self.db_pool.simple_select_onecol(
                table="user_external_ids",
                keyvalues={"auth_provider": self.idp_id},
                retcol="external_id",
            )
        )
        unregistered_users = (
            user
            for user in users
            if self._external_user_id_for_user(user) not in registered_user_subs
        )

        for user in unregistered_users:
            localpart = user["matrix_localpart"]
            display_name = user.get("displayName")

            try:
                existing_user = await self.store.get_user_by_id(
                    UserID(localpart, self.hs.hostname).to_string()
                )
                if existing_user:
                    user_id = existing_user.user_id.to_string()
                else:
                    user_id = await self.hs.get_registration_handler().register_user(
                        localpart=localpart, default_display_name=display_name
                    )
                await self.store.record_user_external_id(
                    auth_provider=self.idp_id,
                    external_id=self._external_user_id_for_user(user),
                    user_id=user_id,
                )
                logger.info(f"Successfully registered {user_id}")
            except Exception as e:
                logger.error(f"Could not register {localpart}: {e}")

    async def reactivate_users(self, users: list[JsonDict]):
        deactivated_users = await UserHelpers.get_deactivated_users(self.db_pool)

        for user in users:
            mxid = UserID(user["matrix_localpart"], self.hs.hostname).to_string()
            if mxid in deactivated_users:
                logger.info(f"Reactivating {mxid}")
                await self.hs.get_deactivate_account_handler().activate_account(mxid)

    async def is_user_expired(self, user: str) -> Optional[bool]:
        # Prevents deactivated users from continuing to use the client-server API.
        deactivated = await UserHelpers.is_user_deactivated(self.store, user)
        if deactivated:
            return True

        return None

    async def toggle_user_active(self, user: JsonDict):
        mxid = UserID(user["matrix_localpart"], self.hs.hostname).to_string()
        is_user_inactive = await UserHelpers.is_user_deactivated(self.store, mxid)

        if user["active"] and is_user_inactive:
            await self.hs.get_deactivate_account_handler().activate_account(mxid)
        elif not user["active"] and not is_user_inactive:
            await self._deactivate_user_by_mxid(mxid)

    def _external_user_id_for_user(self, user: JsonDict) -> str:
        return user["id"]

    async def _mxid_from_username(self, user_name: str) -> Optional[str]:
        user_data = await self.db_pool.simple_select_one_onecol(
            table="connect.profiles",
            keyvalues={"user_id": user_name},
            retcol="data",
        )
        external_id = self._external_user_id_for_user(user_data)
        logger.info(f"Looking up {user_name} with external id {external_id}")
        mxid = await self.db_pool.simple_select_one_onecol(
            table="user_external_ids",
            keyvalues={
                "auth_provider": self.idp_id,
                "external_id": external_id,
            },
            retcol="user_id",
            allow_none=True,
        )
        return mxid
