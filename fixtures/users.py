"""Provisions Matrix users across the auth and identity axes."""

import hashlib
import json
import logging
import secrets
from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict, UserID, create_requester

from connect.fixtures.markers import (
    RUN_MARKER,
    deployment_from_marker,
    owner_from_marker,
    run_marker_content,
)

logger = logging.getLogger(__name__)

# The device every fixtures message is sent from. Synapse deduplicates sends by
# (room, user, device, txn_id) — and the device must exist — so a message
# re-sent after a restart returns the one already in the room.
FIXTURES_DEVICE = "FIXTURES"

# Upper bound on the per-user ``sessions`` (device) count a ``login_token``
# spec may request. Generous for any multi-device convergence test; a cap keeps
# a malformed/abusive scenario from minting unbounded login tokens.
MAX_SESSIONS = 8


class UserProvisioner:
    """Registers Synapse accounts described declaratively by a scenario spec.

    This is the populator's ``register_user`` path generalized over two axes:

    - ``auth`` — ``password`` (a server-chosen password is hashed and returned,
      so the IdP-free CCL harness can log in), ``oidc`` (passwordless; the SCIM
      ``external_id`` is linked so the real IdP can log the user in — exactly the
      populator's model), or ``login_token`` (passwordless; a one-time
      ``m.login.token`` is minted and returned, so the harness logs in via
      ``m.login.token`` — the env-agnostic real login path, since OIDC envs
      advertise it while password login is disabled there).
    - ``identity`` — ``Random`` (a ``run_prefix``-tagged localpart, the default)
      or ``Fixed`` (the caller's ``localpart``, used verbatim — for the named
      users an external consumer hardcodes, e.g. Cypress).

    Every user is created owned by exactly one run: at creation (and on reclaim)
    it is stamped with the ``RUN_MARKER`` account-data tagging the owning
    ``run_id``, which is how run/global teardown finds it (the ``fx_`` localpart
    prefix is a belt-and-suspenders fallback for a user that crashed between
    register and marker-write). A ``Fixed`` name is never silently handed to a
    second run — see ``_resolve_ownership``.

    Provisioning is safe to repeat: a ``Random`` localpart is derived from the
    deployment and ref, so provisioning the same user again after a restart
    finds and adopts the account rather than registering a second one.

    Credentials are never minted while provisioning. ``issue_credentials`` mints
    them when the caller collects them, so none is ever stored, and a login
    token's short life starts when the caller has it. Plaintext passwords and
    login tokens are never logged. Directory enrichment (the audiences ``ExternalUser`` + groups) is
    delegated to an injected enricher when a spec carries a ``directory`` block.
    """

    def __init__(self, hs, idp_id=None, directory=None, profiles=None):
        self.hs = hs
        self.store = hs.get_datastores().main
        self.registration_handler = hs.get_registration_handler()
        self.auth_handler = hs.get_auth_handler()
        self.set_password_handler = hs.get_set_password_handler()
        self.account_data_handler = hs.get_account_data_handler()
        self.deactivate_handler = hs.get_deactivate_account_handler()
        self.device_handler = hs.get_device_handler()
        # ``oidc-<idp>`` auth-provider id, matching the populator; required to
        # link an ``external_id`` (for ``auth: oidc`` and directory enrichment).
        self._idp_id = idp_id
        self._directory = directory
        self._profiles = profiles

    async def provision(
        self,
        spec: JsonDict,
        run_id: str,
        run_prefix: str,
        deployment_id: str,
        admitted_ms: int,
    ) -> JsonDict:
        """Register (or reclaim, or adopt) one user described by ``spec``.

        Args:
            spec (JsonDict): The user spec — ``ref`` plus the optional ``auth``, ``identity``, ``directory``, ``profile`` (the ``connect.profiles`` row — job title, department, territory), and ``display_name``. A ``deactivated`` flag is applied by the orchestrator once the scenario's events exist, not here.
            run_id (str): The owning run; stamped on the user as the run marker.
            run_prefix (str): The run's localpart prefix, used for a ``Random`` identity so the user is cleanable by prefix as a fallback.
            deployment_id (str): The deployment provisioning it; stamped on the marker, and the seed of a ``Random`` localpart.
            admitted_ms (int): When the deployment was admitted. An unmarked account created since then at this localpart is this deployment's own, cut off by a restart before it was marked.

        Returns:
            JsonDict: ``ref``, ``mxid``, ``localpart``, and ``external_id`` when one is linked. Never a credential: see ``issue_credentials``.

        Raises:
            SynapseError: If ``auth`` or ``sessions`` is invalid, directory/``oidc`` is requested without the module configured for it, or the localpart is already owned by another run / not the module's to claim.
        """
        self.validate(spec)
        auth = spec.get("auth", "password")
        profile = spec.get("profile")
        if profile is not None and not self._profiles:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "connect profile seeding is not configured for this environment",
                Codes.UNKNOWN,
            )
        if profile is not None and not self._has_connect_access(spec):
            # The populator's full pass deactivates every active profile whose
            # user is missing from CPG's Connect-access snapshot, so a profile is
            # only safe for a directory user who has Connect access.
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "a user profile needs a directory block with Connect access, or "
                "the populator deactivates the user",
                Codes.INVALID_PARAM,
            )

        fixed = (spec.get("identity") or {}).get("localpart")
        localpart = fixed or self.random_localpart(
            run_prefix, deployment_id, spec.get("ref")
        )
        mxid = UserID(localpart, self.hs.hostname).to_string()
        marker = run_marker_content(run_id, deployment_id)

        # Decide register / reclaim / adopt BEFORE any side effect, so a
        # conflicting name provisions nothing.
        claim = await self._resolve_ownership(
            mxid, run_id, deployment_id, admitted_ms, fixed=bool(fixed)
        )

        directory = spec.get("directory")
        if directory:
            await self._enrich_directory(spec, localpart, directory)

        if claim == "register":
            user_id = await self.registration_handler.register_user(
                localpart=localpart,
                default_display_name=spec.get("display_name"),
            )
        elif claim == "reclaim":
            user_id = await self._reclaim(mxid, marker)
        else:
            user_id = await self._adopt(mxid)

        # Stamp ownership on every user (register, reclaim and adopt), so
        # run/global teardown reaches even a Fixed-name user (no ``fx_`` prefix).
        await self.account_data_handler.add_account_data_for_user(
            user_id, RUN_MARKER, marker
        )
        await self.device_handler.check_device_registered(
            user_id, FIXTURES_DEVICE, "Fixtures"
        )
        logger.info(
            "fixtures provisioned user %s (auth=%s, %s)", localpart, auth, claim
        )

        result = {
            "ref": spec.get("ref"),
            "mxid": user_id,
            "localpart": localpart,
        }

        external_id = self._resolve_external_id(auth, spec, directory, localpart)
        if external_id is not None:
            await self._link_external_id(user_id, external_id)
            await self._upsert_profile(localpart, external_id, spec)
            result["external_id"] = external_id

        if profile is not None:
            await self._profiles.write(localpart, profile, external_id=external_id)

        return result

    def validate(self, spec: JsonDict) -> None:
        """Reject a user spec with an unknown ``auth`` or a bad ``sessions`` count.

        Args:
            spec (JsonDict): The user spec.

        Raises:
            SynapseError: 400 if ``auth`` is unknown or ``sessions`` is invalid.
        """
        auth = spec.get("auth", "password")
        if auth not in ("password", "oidc", "login_token"):
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"unknown user auth '{auth}'",
                Codes.INVALID_PARAM,
            )
        self._resolve_session_count(spec, auth)

    async def issue_credentials(self, spec: JsonDict, mxid: str) -> JsonDict:
        """Mint the credentials ``spec``'s ``auth`` calls for, for ``mxid``.

        Called when the caller collects a finished deployment, never while
        provisioning, so no credential is ever stored. A password is a fresh
        server-chosen one; a login token is one-time-use and short-lived.

        Args:
            spec (JsonDict): The user spec, for its ``auth`` and ``sessions``.
            mxid (str): The provisioned user.

        Returns:
            JsonDict: ``password``, or ``login_token`` (plus ``sessions`` when several devices were asked for), or nothing for ``oidc``.
        """
        auth = spec.get("auth", "password")
        if auth == "password":
            password = secrets.token_urlsafe(16)
            await self.set_password_handler.set_password(
                mxid, await self.auth_handler.hash(password), logout_devices=False
            )
            return {"password": password}
        if auth != "login_token":
            return {}

        token = await self._mint_login_token(mxid)
        credentials: JsonDict = {"login_token": token}
        # A multi-device request also returns the full list (the first entry is
        # the token above). Each token is one-time-use and, when redeemed at
        # ``/login``, mints a fresh device, so several clients can log into the
        # same account concurrently.
        session_count = self._resolve_session_count(spec, auth)
        if session_count is not None:
            sessions = [{"login_token": token}]
            for _ in range(session_count - 1):
                sessions.append({"login_token": await self._mint_login_token(mxid)})
            credentials["sessions"] = sessions
        return credentials

    @staticmethod
    def random_localpart(run_prefix: str, deployment_id: str, ref) -> str:
        """The localpart of a ``Random`` user: run-prefixed, stable per deployment.

        Args:
            run_prefix (str): The run's localpart prefix.
            deployment_id (str): The deployment provisioning the user.
            ref: The user's scenario-local ref.

        Returns:
            str: The same localpart every time this deployment provisions ``ref``.
        """
        # Not security-sensitive: a stable, short tag.
        digest = hashlib.sha1(
            f"{deployment_id}:{ref}".encode("utf-8"), usedforsecurity=False
        )
        return f"{run_prefix}{digest.hexdigest()[:8]}"

    async def deactivate(self, mxid: str) -> None:
        """Deactivate ``mxid`` the way the populator deactivates a departed user.

        The account is kept, not erased, so its display name and the messages it
        sent still render in the rooms it was in. Deactivation parts the user
        from every room, so a scenario calls this only after the user's events
        are sent. The account keeps its run marker: a ``Fixed`` name is then
        reclaimable by a later run (see ``_resolve_ownership``).

        Args:
            mxid (str): The user to deactivate.
        """
        # Safe to repeat after a restart: an already-deactivated user is left as
        # it is (deactivating again would re-run the whole teardown).
        if not await self.store.get_user_deactivated_status(mxid):
            await self.deactivate_handler.deactivate_account(
                mxid, erase_data=False, requester=create_requester(mxid)
            )
        if self._profiles:
            # Like the populator, which flips the profile inactive alongside the
            # Matrix account so the directory stops listing the user.
            await self._profiles.deactivate(UserID.from_string(mxid).localpart)
        logger.info("fixtures deactivated provisioned user %s", mxid)

    @staticmethod
    def _has_connect_access(spec: JsonDict) -> bool:
        directory = spec.get("directory")
        return bool(directory) and directory.get("connect_access", True) is not False

    @staticmethod
    def _resolve_session_count(spec: JsonDict, auth: str):
        # Validate the optional per-user ``sessions`` (device) count. Returns
        # None when unset (the single-device default), else the validated int.
        # Only meaningful for login_token — a count on any other auth is a
        # request error rather than a silently ignored field.
        raw = spec.get("sessions")
        if raw is None:
            return None
        if auth != "login_token":
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "`sessions` (multiple devices) is only supported for auth: login_token",
                Codes.INVALID_PARAM,
            )
        # bool is an int subclass; reject it so `sessions: true` isn't read as 1.
        if (
            not isinstance(raw, int)
            or isinstance(raw, bool)
            or not 1 <= raw <= MAX_SESSIONS
        ):
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"`sessions` must be an integer in 1..={MAX_SESSIONS}, got {raw!r}",
                Codes.INVALID_PARAM,
            )
        return raw

    async def _resolve_ownership(
        self,
        mxid: str,
        run_id: str,
        deployment_id: str,
        admitted_ms: int,
        fixed: bool,
    ) -> str:
        # Decide how to provision `mxid`: "register" a free name, "reclaim" a
        # Fixed name whose owning run was torn down, or "adopt" an account this
        # deployment already made (its earlier attempt at this step was cut off
        # by a restart). Every other state is a 409: the module never silently
        # hands the same named user to two runs — consumers parallelize their
        # own use of it within one run. Concurrent deployments cannot race for a
        # Fixed name: admission reserves it for one unfinished deployment.
        info = await self.store.get_user_by_id(mxid)
        if info is None:
            return "register"

        marker = await self.store.get_global_account_data_by_type_for_user(
            mxid, RUN_MARKER
        )
        if self._made_by(info, marker, deployment_id, admitted_ms):
            return "adopt"

        owner = owner_from_marker(marker)
        if owner is None or not fixed:
            raise SynapseError(
                HTTPStatus.CONFLICT,
                f"{mxid} already exists and was not created by this fixtures "
                "deployment; refusing to claim it.",
                Codes.USER_IN_USE,
            )

        # Only teardown (which erases the account) releases the name. A user
        # seeded `deactivated: true` is deactivated but not erased: still owned.
        if not info.is_deactivated or not await self.store.is_user_erased(mxid):
            raise SynapseError(
                HTTPStatus.CONFLICT,
                f"{mxid} is already provisioned by fixtures run '{owner}'. Tear "
                f"it down with DELETE /_fixtures/run/{owner}, or coordinate use "
                "of it within that run.",
                Codes.LIMIT_EXCEEDED,
            )
        return "reclaim"

    @staticmethod
    def _made_by(info, marker, deployment_id: str, admitted_ms: int) -> bool:
        # This deployment made the account if it says so, or — cut off between
        # registering and marking it — if it is unmarked and newer than the
        # deployment, so no account that predates it can ever be taken over.
        if deployment_from_marker(marker) == deployment_id:
            return True
        return marker is None and info.creation_ts >= admitted_ms // 1000

    async def _reclaim(self, mxid: str, marker: JsonDict) -> str:
        # The owning run was torn down (deactivation released the name), and the
        # name is ours to reclaim. Mark it first, so a restart before the rest
        # finds it already ours and adopts it. Then reactivate the account
        # (clears erased, sets deactivated=False, re-adds to the directory);
        # activate_account does not restore the external_id link, which is
        # re-linked idempotently on the common path.
        await self.account_data_handler.add_account_data_for_user(
            mxid, RUN_MARKER, marker
        )
        await self.deactivate_handler.activate_account(mxid)
        logger.info("fixtures reclaimed user %s", mxid)
        return mxid

    async def _adopt(self, mxid: str) -> str:
        # Finish an account this deployment started: a reclaim cut off before
        # reactivating leaves it deactivated.
        if await self.store.get_user_deactivated_status(mxid):
            await self.deactivate_handler.activate_account(mxid)
        logger.info("fixtures adopted user %s", mxid)
        return mxid

    @staticmethod
    def _resolve_external_id(auth, spec, directory, localpart):
        # Linked for auth: oidc (the IdP login path) and for any user with a
        # directory block, since the directory entry keys on it. The value is the
        # directory's external_id if given, else the spec's, else the localpart.
        if auth != "oidc" and not directory:
            return None
        return (
            (directory or {}).get("external_id") or spec.get("external_id") or localpart
        )

    async def _upsert_profile(
        self, localpart: str, external_id: str, spec: JsonDict
    ) -> None:
        # Audiences reads a human admin's externalId from connect.profiles to decide
        # whether a criterion write would leave the room without one. The populator
        # writes these rows; fixtures must too, or the creator looks admin-less.
        await self.store.db_pool.simple_upsert(
            table="connect.profiles",
            keyvalues={"user_id": localpart},
            values={
                "active": True,
                "data": json.dumps(
                    {
                        "externalId": external_id,
                        "displayName": spec.get("display_name") or localpart,
                    }
                ),
            },
            desc="fixtures_upsert_profile",
        )

    async def _link_external_id(self, user_id: str, external_id: str) -> None:
        # Map external_id to user_id under the OIDC auth provider. Idempotent: a
        # re-link of the same pair (e.g. a reclaimed user) is swallowed so the
        # reclaim path doesn't fail on the still-present link.
        if not self._idp_id:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "external_id linking requires the fixtures module's idp_id "
                "(oidc is not enabled in this environment)",
                Codes.UNKNOWN,
            )
        try:
            await self.store.record_user_external_id(
                auth_provider=self._idp_id,
                external_id=external_id,
                user_id=user_id,
            )
        except Exception:
            # Already linked (a reclaimed user keeps its external_id row).
            existing = await self.store.get_user_by_external_id(
                self._idp_id, external_id
            )
            if existing != user_id:
                raise

    async def _mint_login_token(self, user_id: str) -> str:
        # A one-time ``m.login.token`` the harness redeems via ``m.login.token``
        # login — the same builder the OIDC/SSO completion path uses, so this is
        # a real login on every env that enables OIDC (where password login is
        # off). The token mints a fresh device when redeemed at ``/login``.
        return await self.auth_handler.create_login_token_for_user_id(user_id)

    async def _enrich_directory(
        self, spec: JsonDict, localpart: str, directory: JsonDict
    ) -> None:
        if not self._directory:
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                "directory enrichment is not configured for this environment",
                Codes.UNKNOWN,
            )
        await self._directory.enrich(spec, localpart, directory)
