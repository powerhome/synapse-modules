"""Provisions Matrix users across the auth and identity axes."""

import logging
import secrets
from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError
from synapse.types import JsonDict, UserID

from connect.fixtures.markers import RUN_MARKER, owner_from_marker, run_marker_content

logger = logging.getLogger(__name__)

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
    second run — see ``_resolve_fixed_ownership``.

    Plaintext passwords and login tokens are returned to the caller but never
    logged. Directory enrichment (the audiences ``ExternalUser`` + groups) is
    delegated to an injected enricher when a spec carries a ``directory`` block.
    """

    def __init__(self, hs, idp_id=None, directory=None):
        self.hs = hs
        self.store = hs.get_datastores().main
        self.registration_handler = hs.get_registration_handler()
        self.auth_handler = hs.get_auth_handler()
        self.set_password_handler = hs.get_set_password_handler()
        self.account_data_handler = hs.get_account_data_handler()
        self.deactivate_handler = hs.get_deactivate_account_handler()
        # ``oidc-<idp>`` auth-provider id, matching the populator; required to
        # link an ``external_id`` (for ``auth: oidc`` and directory enrichment).
        self._idp_id = idp_id
        self._directory = directory

    async def provision(self, spec: JsonDict, run_id: str, run_prefix: str) -> JsonDict:
        """Register (or reclaim) one user described by ``spec`` and return it.

        Args:
            spec (JsonDict): The user spec — ``ref`` plus the optional ``auth``, ``identity``, ``directory``, and ``display_name``.
            run_id (str): The owning run; stamped on the user as the run marker.
            run_prefix (str): The run's localpart prefix, used for a ``Random`` identity so the user is cleanable by prefix as a fallback.

        Returns:
            JsonDict: ``ref``, ``mxid``, ``localpart``, and — depending on ``auth`` — ``password``, ``login_token`` (plus a ``sessions`` list of ``{login_token}`` when a ``login_token`` spec requests multiple devices), and/or ``external_id``.

        Raises:
            SynapseError: If ``auth`` is unknown, ``sessions`` is requested for non-``login_token`` auth or is out of range, directory/``oidc`` is requested without the module configured for it, or a ``Fixed`` name is already owned by another run / not the module's to claim.
        """
        auth = spec.get("auth", "password")
        if auth not in ("password", "oidc", "login_token"):
            raise SynapseError(
                HTTPStatus.BAD_REQUEST,
                f"unknown user auth '{auth}'",
                Codes.INVALID_PARAM,
            )

        # Resolved up front so a malformed ``sessions`` count provisions nothing.
        session_count = self._resolve_session_count(spec, auth)

        fixed = (spec.get("identity") or {}).get("localpart")
        localpart = fixed or f"{run_prefix}{secrets.token_hex(4)}"
        mxid = UserID(localpart, self.hs.hostname).to_string()

        # Decide create-vs-reclaim for a Fixed name BEFORE any side effect, so a
        # conflicting name provisions nothing. A Random name never collides.
        reclaim = await self._resolve_fixed_ownership(mxid, run_id) if fixed else False

        directory = spec.get("directory")
        if directory:
            await self._enrich_directory(spec, localpart, directory)

        password = None
        password_hash = None
        if auth == "password":
            password = secrets.token_urlsafe(16)
            password_hash = await self.auth_handler.hash(password)

        if reclaim:
            user_id = await self._reclaim(mxid, password_hash)
        else:
            user_id = await self.registration_handler.register_user(
                localpart=localpart,
                password_hash=password_hash,
                default_display_name=spec.get("display_name"),
            )

        # Stamp ownership on every user (create AND reclaim), so run/global
        # teardown reaches even a Fixed-name user (no ``fx_`` prefix to match).
        await self.account_data_handler.add_account_data_for_user(
            user_id, RUN_MARKER, run_marker_content(run_id)
        )
        logger.info("fixtures provisioned user %s (auth=%s)", localpart, auth)

        result = {
            "ref": spec.get("ref"),
            "mxid": user_id,
            "localpart": localpart,
        }
        if password is not None:
            result["password"] = password

        external_id = self._resolve_external_id(auth, spec, directory, localpart)
        if external_id is not None:
            await self._link_external_id(user_id, external_id)
            result["external_id"] = external_id

        if auth == "login_token":
            token = await self._mint_login_token(user_id)
            result["login_token"] = token
            # A multi-device request also returns the full list (the first entry
            # is the token above). Each token is one-time-use and, when redeemed
            # at ``/login``, mints a fresh device, so several clients can log into
            # the same account concurrently.
            if session_count is not None:
                sessions = [{"login_token": token}]
                for _ in range(session_count - 1):
                    sessions.append(
                        {"login_token": await self._mint_login_token(user_id)}
                    )
                result["sessions"] = sessions

        return result

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

    async def _resolve_fixed_ownership(self, mxid: str, run_id: str) -> bool:
        # Enforce single-run ownership of a Fixed name. Returns False when the
        # name is free to register (does not exist), True when it must be
        # reclaimed (deactivated and ours — its owning run was torn down, which
        # released the name). Every other state is a 409: the module never
        # silently hands the same named user to two runs — consumers parallelize
        # their own use of it within one run.
        #
        # This check and the register/reclaim that follows are NOT atomic: two
        # concurrent provisions of the same brand-new Fixed name could both read
        # "does not exist" and race to register, the loser surfacing as a raw
        # register error rather than this 409. That's acceptable — a Fixed name
        # is single-run by contract, so a run coordinates its own use of it; the
        # check guards against *cross-run* collisions, which are serialized by
        # the prior run's teardown, not by this read.
        info = await self.store.get_user_by_id(mxid)
        if info is None:
            return False

        owner = owner_from_marker(
            await self.store.get_global_account_data_by_type_for_user(mxid, RUN_MARKER)
        )

        if owner is None:
            raise SynapseError(
                HTTPStatus.CONFLICT,
                f"{mxid} already exists and was not created by the fixtures "
                "module; refusing to claim it.",
                Codes.USER_IN_USE,
            )

        if not info.is_deactivated:
            raise SynapseError(
                HTTPStatus.CONFLICT,
                f"{mxid} is already provisioned by fixtures run '{owner}'. Tear "
                f"it down with DELETE /_fixtures/run/{owner}, or coordinate use "
                "of it within that run.",
                Codes.LIMIT_EXCEEDED,
            )

        return True

    async def _reclaim(self, mxid: str, password_hash) -> str:
        # The owning run was torn down (deactivation released the name), and the
        # name is ours to reclaim. Reactivate the account (clears erased, sets
        # deactivated=False, re-adds to the directory), then re-apply the
        # password — activate_account restores neither it nor the external_id
        # link (the latter is re-linked idempotently on the common path). The
        # marker is re-tagged to the new run by the caller.
        await self.deactivate_handler.activate_account(mxid)
        if password_hash is not None:
            await self.set_password_handler.set_password(
                mxid, password_hash, logout_devices=False
            )
        logger.info("fixtures reclaimed user %s", mxid)
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
