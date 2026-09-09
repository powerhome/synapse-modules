"""Tests for the user provisioner."""

import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from synapse.api.errors import SynapseError

from fixtures.markers import RUN_MARKER
from fixtures.users import UserProvisioner


def make_hs(mxid="@fx_abc_dead:localhost", existing_user=None, marker_owner=None):
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

    marker = {"run_id": marker_owner} if marker_owner is not None else None
    store = MagicMock()
    store.get_user_by_id = AsyncMock(return_value=existing_user)
    store.get_global_account_data_by_type_for_user = AsyncMock(return_value=marker)
    store.record_user_external_id = AsyncMock(return_value=None)
    store.get_user_by_external_id = AsyncMock(return_value=mxid)

    hs.get_registration_handler.return_value = registration_handler
    hs.get_auth_handler.return_value = auth_handler
    hs.get_set_password_handler.return_value = set_password_handler
    hs.get_account_data_handler.return_value = account_data_handler
    hs.get_deactivate_account_handler.return_value = deactivate_handler
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


def active_user():
    info = MagicMock()
    info.is_deactivated = False
    return info


def deactivated_user():
    info = MagicMock()
    info.is_deactivated = True
    return info


class PasswordAuthTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_registers_with_a_hashed_password_and_returns_credentials(self):
        provisioner, h = make_provisioner()

        result = await provisioner.provision({"ref": "alice"}, "run-1", "fx_abc_")

        kwargs = h.registration.register_user.await_args.kwargs
        self.assertEqual(kwargs["password_hash"], "hashed-password")
        self.assertTrue(kwargs["localpart"].startswith("fx_abc_"))
        self.assertEqual(result["ref"], "alice")
        self.assertEqual(result["mxid"], "@fx_abc_dead:localhost")
        self.assertTrue(result["password"])

    async def test_password_is_never_the_stored_hash(self):
        provisioner, _ = make_provisioner()

        result = await provisioner.provision({"ref": "alice"}, "run-1", "fx_abc_")

        self.assertNotEqual(result["password"], "hashed-password")


class IdentityTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_random_identity_uses_the_run_prefix(self):
        provisioner, h = make_provisioner()

        await provisioner.provision({"ref": "a"}, "run-1", "fx_run_")

        localpart = h.registration.register_user.await_args.kwargs["localpart"]
        self.assertTrue(localpart.startswith("fx_run_"))

    async def test_fixed_identity_uses_the_caller_localpart_verbatim(self):
        provisioner, h = make_provisioner(mxid="@bob.smith:localhost")

        await provisioner.provision(
            {"ref": "a", "identity": {"localpart": "bob.smith"}}, "run-1", "fx_run_"
        )

        localpart = h.registration.register_user.await_args.kwargs["localpart"]
        self.assertEqual(localpart, "bob.smith")


class RunMarkerTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_random_user_is_tagged_with_the_run_marker(self):
        provisioner, h = make_provisioner()

        await provisioner.provision({"ref": "a"}, "run-1", "fx_run_")

        h.account_data.add_account_data_for_user.assert_awaited_once_with(
            "@fx_abc_dead:localhost", RUN_MARKER, {"run_id": "run-1"}
        )

    async def test_fixed_user_create_is_tagged_with_the_run_marker(self):
        provisioner, h = make_provisioner(mxid="@bob.smith:localhost")

        await provisioner.provision(
            {"ref": "a", "identity": {"localpart": "bob.smith"}}, "run-1", "fx_run_"
        )

        h.account_data.add_account_data_for_user.assert_awaited_once_with(
            "@bob.smith:localhost", RUN_MARKER, {"run_id": "run-1"}
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
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
        self.assertIn("not created by the fixtures module", ctx.exception.msg)
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
            )

        self.assertEqual(ctx.exception.code, HTTPStatus.CONFLICT)
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
        )

        h.deactivate.activate_account.assert_awaited_once_with(
            "@andrew.kloecker:localhost"
        )
        h.registration.register_user.assert_not_awaited()
        # Reactivation must re-set the password (activate_account does not).
        h.set_password.set_password.assert_awaited_once()
        # Re-tagged to the NEW run.
        h.account_data.add_account_data_for_user.assert_awaited_once_with(
            "@andrew.kloecker:localhost", RUN_MARKER, {"run_id": "cypress-2"}
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
        )

        self.assertIsNone(
            h.registration.register_user.await_args.kwargs["password_hash"]
        )
        h.store.record_user_external_id.assert_awaited_once()
        self.assertEqual(result["external_id"], "bob.smith")
        self.assertNotIn("password", result)

    async def test_oidc_without_idp_id_is_rejected(self):
        provisioner, _ = make_provisioner(idp_id=None)

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision({"ref": "a", "auth": "oidc"}, "run-1", "fx_")
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


class LoginTokenAuthTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_mints_a_login_token(self):
        provisioner, _ = make_provisioner()

        result = await provisioner.provision(
            {"ref": "a", "auth": "login_token"}, "run-1", "fx_"
        )

        self.assertEqual(result["login_token"], "login_tok")
        self.assertNotIn("password", result)
        self.assertNotIn("access_token", result)
        self.assertNotIn("device_id", result)

    async def test_single_device_has_no_sessions_list(self):
        # Without a `sessions` count the single-device shape is unchanged: the
        # top-level token only, no `sessions` array.
        provisioner, _ = make_provisioner()

        result = await provisioner.provision(
            {"ref": "a", "auth": "login_token"}, "run-1", "fx_"
        )

        self.assertNotIn("sessions", result)

    async def test_sessions_count_mints_one_login_token_per_device(self):
        provisioner, h = make_provisioner()
        h.auth.create_login_token_for_user_id.side_effect = ["tok1", "tok2"]

        result = await provisioner.provision(
            {"ref": "a", "auth": "login_token", "sessions": 2}, "run-1", "fx_"
        )

        self.assertEqual(h.auth.create_login_token_for_user_id.await_count, 2)
        # The first minted token is also the top-level token (back-compatible).
        self.assertEqual(result["login_token"], "tok1")
        self.assertEqual(
            result["sessions"],
            [{"login_token": "tok1"}, {"login_token": "tok2"}],
        )

    async def test_sessions_on_non_login_token_auth_is_rejected(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "auth": "password", "sessions": 2}, "run-1", "fx_"
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
                )
            self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


class UnknownAuthTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_auth_is_a_bad_request(self):
        provisioner, _ = make_provisioner()

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision({"ref": "a", "auth": "magic"}, "run-1", "fx_")
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)


class DirectoryTestSuite(unittest.IsolatedAsyncioTestCase):
    async def test_directory_without_an_enricher_is_rejected(self):
        provisioner, _ = make_provisioner(directory=None)

        with self.assertRaises(SynapseError) as ctx:
            await provisioner.provision(
                {"ref": "a", "directory": {"groups": []}}, "run-1", "fx_"
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
        )

        enricher.enrich.assert_awaited_once()
        h.store.record_user_external_id.assert_awaited_once()
        self.assertEqual(result["external_id"], "bob.smith")


if __name__ == "__main__":
    unittest.main()
