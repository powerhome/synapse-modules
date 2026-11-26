"""Tests for the user provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import SynapseError

from fixtures.markers import RUN_MARKER
from fixtures.users import UserProvisioner

# When the deployment was admitted: accounts created before it are never adopted.
ADMITTED_MS = 1_700_000_000_000


def make_hs(
    mxid="@fx_abc_dead:localhost",
    existing_user=None,
    marker_owner=None,
    marker_deployment="dep-old",
):
    hs = MagicMock()
    hs.hostname = "localhost"

    registration_handler = MagicMock()
    registration_handler.register_user = AsyncMock(return_value=mxid)
    auth_handler = MagicMock()
    auth_handler.hash = AsyncMock(return_value="hashed-password")
    auth_handler.create_login_token_for_user_id = AsyncMock(return_value="login_tok")
    set_password_handler = MagicMock()
    set_password_handler.set_password = AsyncMock(return_value=None)
    account_data_handler = MagicMock()
    account_data_handler.add_account_data_for_user = AsyncMock(return_value=1)
    deactivate_handler = MagicMock()
    deactivate_handler.activate_account = AsyncMock(return_value=None)
    deactivate_handler.deactivate_account = AsyncMock(return_value=None)
    device_handler = MagicMock()
    device_handler.check_device_registered = AsyncMock(return_value="FIXTURES")

    marker = (
        {"run_id": marker_owner, "deployment_id": marker_deployment}
        if marker_owner is not None
        else None
    )
    store = MagicMock()
    store.get_user_by_id = AsyncMock(return_value=existing_user)
    # Teardown erases a user, which is what releases a Fixed name for reclaim.
    store.is_user_erased = AsyncMock(return_value=True)
    store.get_global_account_data_by_type_for_user = AsyncMock(return_value=marker)
    store.record_user_external_id = AsyncMock(return_value=None)
    store.get_user_by_external_id = AsyncMock(return_value=mxid)
    store.get_user_deactivated_status = AsyncMock(return_value=False)
    store.db_pool.simple_upsert = AsyncMock(return_value=None)

    hs.get_registration_handler.return_value = registration_handler
    hs.get_auth_handler.return_value = auth_handler
    hs.get_set_password_handler.return_value = set_password_handler
    hs.get_account_data_handler.return_value = account_data_handler
    hs.get_deactivate_account_handler.return_value = deactivate_handler
    hs.get_device_handler.return_value = device_handler
    hs.get_datastores.return_value.main = store
    return Handles(
        hs,
        registration_handler,
        auth_handler,
        store,
        set_password_handler,
        account_data_handler,
        deactivate_handler,
    )


class Handles:
    # A small bag of the mocks a test wants to assert against, so each test can
    # name only the ones it uses.
    def __init__(
        self,
        hs,
        registration_handler,
        auth_handler,
        store,
        set_password_handler,
        account_data_handler,
        deactivate_handler,
    ):
        self.hs = hs
        self.registration = registration_handler
        self.auth = auth_handler
        self.store = store
        self.set_password = set_password_handler
        self.account_data = account_data_handler
        self.deactivate = deactivate_handler


def make_provisioner(idp_id="oidc-cpg", directory=None, **kwargs):
    h = make_hs(**kwargs)
    provisioner = UserProvisioner(h.hs, idp_id=idp_id, directory=directory)
    return provisioner, h


def active_user(creation_ts=0):
    info = MagicMock()
    info.is_deactivated = False
    info.creation_ts = creation_ts
    return info


def deactivated_user(creation_ts=0):
    info = MagicMock()
    info.is_deactivated = True
    info.creation_ts = creation_ts
    return info


def marker(run_id="run-1", deployment_id="dep-1"):
    return {"run_id": run_id, "deployment_id": deployment_id}


class RegistrationTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_registers_without_a_password_and_returns_no_credentials(self):
        provisioner, h = make_provisioner()

        result = await provisioner.provision(
            {"ref": "alice"}, "run-1", "fx_abc_", "dep-1", ADMITTED_MS
        )

        kwargs = h.registration.register_user.await_args.kwargs
        self.assertNotIn("password_hash", kwargs)
        self.assertTrue(kwargs["localpart"].startswith("fx_abc_"))
        self.assertEqual(result["ref"], "alice")
        self.assertEqual(result["mxid"], "@fx_abc_dead:localhost")
        self.assertNotIn("password", result)
        self.assertNotIn("login_token", result)

    async def test_registers_the_fixtures_device_for_deduplicated_sends(self):
        provisioner, h = make_provisioner()

        await provisioner.provision(
            {"ref": "alice"}, "run-1", "fx_abc_", "dep-1", ADMITTED_MS
        )

        h.hs.get_device_handler.return_value.check_device_registered.assert_awaited_once_with(
            "@fx_abc_dead:localhost", "FIXTURES", "Fixtures"
        )


class IdentityTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_random_identity_uses_the_run_prefix(self):
        provisioner, h = make_provisioner()

        await provisioner.provision(
            {"ref": "a"}, "run-1", "fx_run_", "dep-1", ADMITTED_MS
        )

        localpart = h.registration.register_user.await_args.kwargs["localpart"]
        self.assertTrue(localpart.startswith("fx_run_"))

    async def test_random_localpart_is_the_same_each_time_for_a_deployment_and_ref(
        self,
    ):
        first = UserProvisioner.random_localpart("fx_run_", "dep-1", "alice")
        again = UserProvisioner.random_localpart("fx_run_", "dep-1", "alice")
        other_ref = UserProvisioner.random_localpart("fx_run_", "dep-1", "bob")
        other_deployment = UserProvisioner.random_localpart("fx_run_", "dep-2", "alice")

        self.assertEqual(first, again)
        self.assertNotEqual(first, other_ref)
        self.assertNotEqual(first, other_deployment)

    async def test_fixed_identity_uses_the_caller_localpart_verbatim(self):
        provisioner, h = make_provisioner(mxid="@bob.smith:localhost")

        await provisioner.provision(
            {"ref": "a", "identity": {"localpart": "bob.smith"}},
            "run-1",
            "fx_run_",
            "dep-1",
            ADMITTED_MS,
        )

        localpart = h.registration.register_user.await_args.kwargs["localpart"]
        self.assertEqual(localpart, "bob.smith")


class RunMarkerTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_random_user_is_tagged_with_the_run_marker(self):
        provisioner, h = make_provisioner()

        await provisioner.provision(
            {"ref": "a"}, "run-1", "fx_run_", "dep-1", ADMITTED_MS
        )

        h.account_data.add_account_data_for_user.assert_awaited_once_with(
            "@fx_abc_dead:localhost", RUN_MARKER, marker()
        )

    async def test_fixed_user_create_is_tagged_with_the_run_marker(self):
        provisioner, h = make_provisioner(mxid="@bob.smith:localhost")

        await provisioner.provision(
            {"ref": "a", "identity": {"localpart": "bob.smith"}},
            "run-1",
            "fx_run_",
            "dep-1",
            ADMITTED_MS,
        )

        h.account_data.add_account_data_for_user.assert_awaited_once_with(
            "@bob.smith:localhost", RUN_MARKER, marker()
        )


class FixedOwnershipTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_active_owned_by_another_run_is_a_conflict_naming_the_run(self):
        provisioner, h = make_provisioner(
            mxid="@andrew.kloecker:localhost",
            existing_user=active_user(),
            marker_owner="cypress-1",
        )

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "identity": {"localpart": "andrew.kloecker"}},
                "cypress-2",
                "fx_run_",
                "dep-1",
                ADMITTED_MS,
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
        self.assertIn("cypress-1", ctx.exception.msg)
        h.registration.register_user.assert_not_awaited()
        h.account_data.add_account_data_for_user.assert_not_awaited()

    async def test_active_unmarked_account_is_refused(self):
        provisioner, h = make_provisioner(
            mxid="@real.person:localhost",
            existing_user=active_user(),
            marker_owner=None,
        )

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "identity": {"localpart": "real.person"}},
                "run-1",
                "fx_run_",
                "dep-1",
                ADMITTED_MS,
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
        self.assertIn("not created by this fixtures deployment", ctx.exception.msg)
        h.registration.register_user.assert_not_awaited()

    async def test_deactivated_unmarked_account_is_refused(self):
        provisioner, h = make_provisioner(
            mxid="@real.person:localhost",
            existing_user=deactivated_user(),
            marker_owner=None,
        )

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "identity": {"localpart": "real.person"}},
                "run-1",
                "fx_run_",
                "dep-1",
                ADMITTED_MS,
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
        h.deactivate.activate_account.assert_not_awaited()

    async def test_deactivated_but_not_erased_is_still_owned_and_a_conflict(self):
        # A user seeded `deactivated: true` is deactivated yet still owned by its
        # run: only teardown (which erases) releases the name.
        provisioner, h = make_provisioner(
            mxid="@andrew.kloecker:localhost",
            existing_user=deactivated_user(),
            marker_owner="cypress-1",
        )
        h.store.is_user_erased = AsyncMock(return_value=False)

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "identity": {"localpart": "andrew.kloecker"}},
                "cypress-2",
                "fx_run_",
                "dep-1",
                ADMITTED_MS,
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
        self.assertIn("cypress-1", ctx.exception.msg)
        h.deactivate.activate_account.assert_not_awaited()

    async def test_deactivated_and_owned_is_reclaimed_and_retagged(self):
        provisioner, h = make_provisioner(
            mxid="@andrew.kloecker:localhost",
            existing_user=deactivated_user(),
            marker_owner="cypress-1",
        )

        result = await provisioner.provision(
            {"ref": "a", "identity": {"localpart": "andrew.kloecker"}},
            "cypress-2",
            "fx_run_",
            "dep-1",
            ADMITTED_MS,
        )

        h.deactivate.activate_account.assert_awaited_once_with(
            "@andrew.kloecker:localhost"
        )
        h.registration.register_user.assert_not_awaited()
        # Re-tagged to the NEW run before reactivating, so a restart part-way
        # through finds the account already this deployment's.
        h.account_data.add_account_data_for_user.assert_awaited_with(
            "@andrew.kloecker:localhost", RUN_MARKER, marker("cypress-2")
        )
        self.assertEqual(result["mxid"], "@andrew.kloecker:localhost")

    async def test_reclaim_relinks_external_id_for_oidc(self):
        provisioner, h = make_provisioner(
            mxid="@andrew.kloecker:localhost",
            existing_user=deactivated_user(),
            marker_owner="cypress-1",
        )

        result = await provisioner.provision(
            {
                "ref": "a",
                "auth": "oidc",
                "identity": {"localpart": "andrew.kloecker"},
            },
            "cypress-2",
            "fx_run_",
            "dep-1",
            ADMITTED_MS,
        )

        h.deactivate.activate_account.assert_awaited_once()
        h.set_password.set_password.assert_not_awaited()
        h.store.record_user_external_id.assert_awaited_once()
        self.assertEqual(result["external_id"], "andrew.kloecker")


class OidcAuthTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_registers_passwordless_and_links_the_external_id(self):
        provisioner, h = make_provisioner(mxid="@bob.smith:localhost")

        result = await provisioner.provision(
            {"ref": "a", "auth": "oidc", "identity": {"localpart": "bob.smith"}},
            "run-1",
            "fx_",
            "dep-1",
            ADMITTED_MS,
        )

        self.assertNotIn(
            "password_hash", h.registration.register_user.await_args.kwargs
        )
        h.store.record_user_external_id.assert_awaited_once()
        profile = h.store.db_pool.simple_upsert.await_args.kwargs
        self.assertEqual(profile["table"], "connect.profiles")
        self.assertEqual(profile["keyvalues"], {"user_id": "bob.smith"})
        self.assertEqual(result["external_id"], "bob.smith")
        self.assertNotIn("password", result)

    async def test_oidc_without_idp_id_is_rejected(self):
        provisioner, _ = make_provisioner(idp_id=None)

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "auth": "oidc"}, "run-1", "fx_", "dep-1", ADMITTED_MS
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


class AdoptTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_adopts_its_own_account_instead_of_registering_again(self):
        provisioner, h = make_provisioner(
            existing_user=active_user(), marker_owner="run-1", marker_deployment="dep-1"
        )

        result = await provisioner.provision(
            {"ref": "a"}, "run-1", "fx_abc_", "dep-1", ADMITTED_MS
        )

        h.registration.register_user.assert_not_awaited()
        self.assertTrue(result["mxid"].startswith("@fx_abc_"))

    async def test_adopts_an_unmarked_account_created_since_admission(self):
        # Cut off between registering the account and marking it.
        provisioner, h = make_provisioner(
            existing_user=active_user(creation_ts=ADMITTED_MS // 1000 + 5)
        )

        await provisioner.provision(
            {"ref": "a"}, "run-1", "fx_abc_", "dep-1", ADMITTED_MS
        )

        h.registration.register_user.assert_not_awaited()
        h.account_data.add_account_data_for_user.assert_awaited_once()

    async def test_never_adopts_an_unmarked_account_older_than_the_deployment(self):
        provisioner, h = make_provisioner(
            mxid="@real.person:localhost",
            existing_user=active_user(creation_ts=ADMITTED_MS // 1000 - 60),
        )

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "identity": {"localpart": "real.person"}},
                "run-1",
                "fx_",
                "dep-1",
                ADMITTED_MS,
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
        h.account_data.add_account_data_for_user.assert_not_awaited()

    async def test_finishes_a_reclaim_cut_off_before_reactivating(self):
        provisioner, h = make_provisioner(
            mxid="@andrew.kloecker:localhost",
            existing_user=deactivated_user(),
            marker_owner="run-1",
            marker_deployment="dep-1",
        )
        h.store.get_user_deactivated_status = AsyncMock(return_value=True)

        await provisioner.provision(
            {"ref": "a", "identity": {"localpart": "andrew.kloecker"}},
            "run-1",
            "fx_",
            "dep-1",
            ADMITTED_MS,
        )

        h.deactivate.activate_account.assert_awaited_once_with(
            "@andrew.kloecker:localhost"
        )

    async def test_another_deployments_random_name_is_a_conflict(self):
        provisioner, h = make_provisioner(
            existing_user=active_user(), marker_owner="run-1", marker_deployment="dep-9"
        )

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a"}, "run-1", "fx_abc_", "dep-1", ADMITTED_MS
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)


class IssueCredentialsTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_a_password_user_gets_a_fresh_password_set_on_the_account(self):
        provisioner, h = make_provisioner()

        credentials = await provisioner.issue_credentials(
            {"ref": "a", "auth": "password"}, "@a:localhost"
        )

        self.assertTrue(credentials["password"])
        self.assertNotEqual(credentials["password"], "hashed-password")
        h.set_password.set_password.assert_awaited_once_with(
            "@a:localhost", "hashed-password", logout_devices=False
        )

    async def test_auth_defaults_to_password(self):
        provisioner, _ = make_provisioner()

        credentials = await provisioner.issue_credentials({"ref": "a"}, "@a:localhost")

        self.assertIn("password", credentials)

    async def test_a_login_token_user_gets_a_login_token(self):
        provisioner, _ = make_provisioner()

        credentials = await provisioner.issue_credentials(
            {"ref": "a", "auth": "login_token"}, "@a:localhost"
        )

        self.assertEqual(credentials, {"login_token": "login_tok"})

    async def test_sessions_count_mints_one_login_token_per_device(self):
        provisioner, h = make_provisioner()
        h.auth.create_login_token_for_user_id.side_effect = ["tok1", "tok2"]

        credentials = await provisioner.issue_credentials(
            {"ref": "a", "auth": "login_token", "sessions": 2}, "@a:localhost"
        )

        # The first minted token is also the top-level token (back-compatible).
        self.assertEqual(credentials["login_token"], "tok1")
        self.assertEqual(
            credentials["sessions"],
            [{"login_token": "tok1"}, {"login_token": "tok2"}],
        )

    async def test_an_oidc_user_gets_none(self):
        provisioner, h = make_provisioner()

        credentials = await provisioner.issue_credentials(
            {"ref": "a", "auth": "oidc"}, "@a:localhost"
        )

        self.assertEqual(credentials, {})
        h.set_password.set_password.assert_not_awaited()


class SessionsValidationTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_sessions_on_non_login_token_auth_is_rejected(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "auth": "password", "sessions": 2},
                "run-1",
                "fx_",
                "dep-1",
                ADMITTED_MS,
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_sessions_out_of_range_is_rejected(self):
        provisioner, _ = make_provisioner()

        for bad in (0, 99, True, "2"):
            with self.assertRaises(SynapseError) as ctx:
                await provisioner.provision(
                    {"ref": "a", "auth": "login_token", "sessions": bad},
                    "run-1",
                    "fx_",
                    "dep-1",
                    ADMITTED_MS,
                )
            self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


def make_profiles():
    profiles = MagicMock()
    profiles.write = AsyncMock()
    profiles.deactivate = AsyncMock()
    return profiles


def make_directory_provisioner(hs, profiles):
    return UserProvisioner(
        hs,
        idp_id="oidc-cpg",
        directory=MagicMock(enrich=AsyncMock()),
        profiles=profiles,
    )


class ProfileTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_writes_the_profile_for_the_registered_localpart(self):
        profiles = make_profiles()
        h = make_hs(mxid="@fx_abc_dead:localhost")
        provisioner = make_directory_provisioner(h.hs, profiles)
        profile = {"job_title": "Foreman", "department": "Installation"}

        await provisioner.provision(
            {"ref": "a", "directory": {"groups": []}, "profile": profile},
            "run-1",
            "fx_abc_",
            "dep-1",
            ADMITTED_MS,
        )

        profiles.write.assert_awaited_once()
        args = profiles.write.await_args
        self.assertTrue(args.args[0].startswith("fx_abc_"))
        self.assertEqual(args.args[1], profile)

    async def test_writes_an_empty_profile_block(self):
        profiles = make_profiles()
        provisioner = make_directory_provisioner(make_hs().hs, profiles)

        await provisioner.provision(
            {"ref": "a", "directory": {"groups": []}, "profile": {}},
            "run-1",
            "fx_abc_",
            "dep-1",
            ADMITTED_MS,
        )

        profiles.write.assert_awaited_once()

    async def test_passes_the_linked_external_id_so_the_row_matches_the_directory(
        self,
    ):
        profiles = make_profiles()
        provisioner = UserProvisioner(
            make_hs().hs,
            idp_id="oidc-cpg",
            directory=MagicMock(enrich=AsyncMock()),
            profiles=profiles,
        )

        await provisioner.provision(
            {
                "ref": "a",
                "auth": "oidc",
                "identity": {"localpart": "bob.smith"},
                "directory": {"external_id": "1234"},
                "profile": {},
            },
            "run-1",
            "fx_abc_",
            "dep-1",
            ADMITTED_MS,
        )

        self.assertEqual(profiles.write.await_args.kwargs["external_id"], "1234")

    async def test_writes_no_profile_when_the_spec_has_none(self):
        profiles = make_profiles()
        provisioner = UserProvisioner(make_hs().hs, profiles=profiles)

        await provisioner.provision(
            {"ref": "a"}, "run-1", "fx_abc_", "dep-1", ADMITTED_MS
        )

        profiles.write.assert_not_awaited()

    async def test_a_profile_without_a_profile_writer_is_a_bad_request(self):
        provisioner = UserProvisioner(make_hs().hs)

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "profile": {}}, "run-1", "fx_abc_", "dep-1", ADMITTED_MS
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_a_profile_without_a_directory_block_is_a_bad_request(self):
        profiles = make_profiles()
        provisioner = make_directory_provisioner(make_hs().hs, profiles)

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "profile": {}}, "run-1", "fx_abc_", "dep-1", ADMITTED_MS
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        profiles.write.assert_not_awaited()

    async def test_a_profile_for_a_user_without_connect_access_is_a_bad_request(self):
        profiles = make_profiles()
        provisioner = make_directory_provisioner(make_hs().hs, profiles)

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "directory": {"connect_access": False}, "profile": {}},
                "run-1",
                "fx_abc_",
                "dep-1",
                ADMITTED_MS,
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)
        profiles.write.assert_not_awaited()

    async def test_deactivating_a_user_also_deactivates_their_profile(self):
        profiles = make_profiles()
        provisioner = UserProvisioner(make_hs().hs, profiles=profiles)

        await provisioner.deactivate("@alice:localhost")

        profiles.deactivate.assert_awaited_once_with("alice")


class DeactivateTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_deactivates_the_account_without_erasing_it(self):
        handles = make_hs()
        provisioner = UserProvisioner(handles.hs)

        await provisioner.deactivate("@alice:localhost")

        # Like the populator's deactivation of a departed employee: the account
        # is kept (not erased), so its display name and history still render.
        deactivate_account = handles.deactivate.deactivate_account
        deactivate_account.assert_awaited_once()
        self.assertEqual(deactivate_account.await_args.args, ("@alice:localhost",))
        self.assertFalse(deactivate_account.await_args.kwargs["erase_data"])
        self.assertEqual(
            deactivate_account.await_args.kwargs["requester"].user.to_string(),
            "@alice:localhost",
        )

    async def test_an_already_deactivated_user_is_left_alone(self):
        handles = make_hs()
        handles.store.get_user_deactivated_status = AsyncMock(return_value=True)
        provisioner = UserProvisioner(handles.hs)

        await provisioner.deactivate("@alice:localhost")

        handles.deactivate.deactivate_account.assert_not_awaited()


class UnknownAuthTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_auth_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "auth": "magic"}, "run-1", "fx_", "dep-1", ADMITTED_MS
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


class DirectoryTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_directory_without_an_enricher_is_rejected(self):
        provisioner, _ = make_provisioner(directory=None)

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "directory": {"groups": []}},
                "run-1",
                "fx_",
                "dep-1",
                ADMITTED_MS,
            )
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    async def test_directory_delegates_to_the_enricher_and_links_external_id(self):
        enricher = MagicMock()
        enricher.enrich = AsyncMock(return_value=None)
        provisioner, h = make_provisioner(
            directory=enricher, mxid="@bob.smith:localhost"
        )

        result = await provisioner.provision(
            {
                "ref": "a",
                "identity": {"localpart": "bob.smith"},
                "directory": {"groups": ["Departments:BT"], "external_id": "bob.smith"},
            },
            "run-1",
            "fx_",
            "dep-1",
            ADMITTED_MS,
        )

        enricher.enrich.assert_awaited_once()
        h.store.record_user_external_id.assert_awaited_once()
        self.assertEqual(result["external_id"], "bob.smith")


if __name__ == "__main__":
    unittest.main()
