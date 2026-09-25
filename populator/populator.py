"""Populator module."""

import json
import logging
from typing import TYPE_CHECKING, Any

from synapse.types import JsonDict, Optional, UserID, create_requester
from twisted.internet import defer

from ..audiences.audiences_bot import AudiencesBot
from ..helpers.request_context import cpg_headers, init_request_context, log_event
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
        defer.ensureDeferred(self._register_synapse_audience_client())

    async def _register_synapse_audience_client(self):
        init_request_context(action="request_synapse_audience")
        try:
            merged_headers = {**self.headers, **cpg_headers()}
            log_event(
                logger,
                "Requesting audience from CPG",
                endpoint="/audiences/api/clients",
            )
            body = await self.client.post_json_get_json(
                uri="http://audiences:3000/audiences/api/clients",
                post_json={},
                headers=merged_headers,
            )
            assert body["name"] == "synapse"
            log_event(logger, "Audience request succeeded")
        except Exception as e:
            # TODO improve retry logic

            log_event(
                logger,
                "Could not connect to audiences, retrying",
                level=logging.WARNING,
                error=str(e),
            )
            await self.api.sleep(60.0)
            await self._register_synapse_audience_client()

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
        user_id = user.get("id")
        log_event(logger, "Single user provisioning started", user_id=user_id)

        await self.inject_localparts([user])
        await self.populate([user])
        await self.update_display_names([user])
        await self.register_unregistered_users([user])
        await self.toggle_user_active(user)

        log_event(logger, "Single user provisioning completed", user_id=user_id)

    async def populate_all_users(self, users: list[JsonDict]):
        log_event(logger, "Bulk provisioning started", user_count=len(users))

        await self.inject_localparts(users)
        await self.populate(users)
        await self.deactivate_users(users)
        await self.update_display_names(users)
        await self.register_unregistered_users(users)
        await self.reactivate_users(users)

        log_event(logger, "Bulk provisioning completed", user_count=len(users))

    async def populate(self, users: list[JsonDict]):
        log_event(logger, "Populating user profiles", user_count=len(users))

        profile_user_id_rows = [[user["matrix_localpart"]] for user in users]
        profile_active_and_data_rows = [
            [user["active"], json.dumps(user)] for user in users
        ]

        await self.db_pool.simple_upsert_many(
            table="connect.profiles",
            key_names=["user_id"],
            key_values=profile_user_id_rows,
            value_names=["active", "data"],
            value_values=profile_active_and_data_rows,
            desc="",
        )

        log_event(logger, "User profiles populated", user_count=len(users))

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

        log_event(
            logger,
            "Deactivating users",
            deactivate_count=len(inactive_user_ids),
        )

        await self.db_pool.runInteraction(
            "deactivate_users_txn", self._deactivate_users_txn, inactive_user_ids
        )

        deactivated_users = await UserHelpers.get_deactivated_users(self.db_pool)
        for localpart in inactive_user_ids:
            mxid = UserID(localpart, self.hs.hostname).to_string()
            if mxid not in deactivated_users:
                try:
                    await self._deactivate_user_by_mxid(mxid)
                except Exception as e:
                    log_event(
                        logger,
                        "Could not deactivate user in Synapse, skipping",
                        level=logging.WARNING,
                        mxid=mxid,
                        error=str(e),
                    )

    def _deactivate_users_txn(self, txn, inactive_user_ids: list[str]):
        inactive_profile_user_id_rows = [[localpart] for localpart in inactive_user_ids]
        profile_set_active_false_rows = [[False] for _ in inactive_user_ids]

        self.db_pool.simple_update_many_txn(
            txn=txn,
            table="connect.profiles",
            key_names=["user_id"],
            key_values=inactive_profile_user_id_rows,
            value_names=["active"],
            value_values=profile_set_active_false_rows,
        )

    async def _deactivate_user_by_mxid(self, mxid: str):
        log_event(logger, "Deactivating user in Synapse", mxid=mxid)
        audiences_bot_mxid = AudiencesBot(self.config, self.hs).bot_mxid
        await self.hs.get_deactivate_account_handler().deactivate_account(
            mxid, erase_data=False, requester=create_requester(audiences_bot_mxid)
        )
        log_event(logger, "User deactivated in Synapse", mxid=mxid)

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
                log_event(logger, "Updating display name", user_id=user_id)
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
                    log_event(
                        logger,
                        "User already exists, linking external ID",
                        user_id=user_id,
                    )
                else:
                    user_id = await self.hs.get_registration_handler().register_user(
                        localpart=localpart, default_display_name=display_name
                    )
                    log_event(logger, "User registered in Synapse", user_id=user_id)
                await self.store.record_user_external_id(
                    auth_provider=self.idp_id,
                    external_id=self._external_user_id_for_user(user),
                    user_id=user_id,
                )
            except Exception as e:
                log_event(
                    logger,
                    "Could not register user",
                    level=logging.ERROR,
                    localpart=localpart,
                    error=str(e),
                )

    async def reactivate_users(self, users: list[JsonDict]):
        deactivated_users = await UserHelpers.get_deactivated_users(self.db_pool)

        for user in users:
            mxid = UserID(user["matrix_localpart"], self.hs.hostname).to_string()
            if mxid in deactivated_users:
                log_event(logger, "Reactivating user in Synapse", mxid=mxid)
                await self.hs.get_deactivate_account_handler().activate_account(mxid)
                log_event(logger, "User reactivated in Synapse", mxid=mxid)

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
            log_event(logger, "Reactivating user (single)", mxid=mxid)
            await self.hs.get_deactivate_account_handler().activate_account(mxid)
            log_event(logger, "User reactivated (single)", mxid=mxid)
        elif not user["active"] and not is_user_inactive:
            await self._deactivate_user_by_mxid(mxid)

    def _external_user_id_for_user(self, user: JsonDict) -> str:
        return user["id"]
