"""Unit tests for auth introspect module registration."""

import unittest
from unittest.mock import MagicMock

from connect.auth_introspect import Module
from connect.auth_introspect.api import INTROSPECT_PATH, IntrospectResource


def make_api(worker_name):
    api = MagicMock()
    api.worker_name = worker_name
    return api


class IntrospectModuleTestSuite(unittest.TestCase):
    def test_registers_on_the_generic_worker(self):
        api = make_api("synapse-generic-worker")

        Module({}, api)

        api.register_web_resource.assert_called_once()
        kwargs = api.register_web_resource.call_args.kwargs
        self.assertEqual(kwargs["path"], INTROSPECT_PATH)
        self.assertIsInstance(kwargs["resource"], IntrospectResource)

    def test_path_is_the_one_the_sidecar_calls(self):
        self.assertEqual(INTROSPECT_PATH, "/_connect/auth/introspect")

    def test_does_not_register_on_the_main_process_or_other_workers(self):
        for worker_name in (None, "synapse-sync-worker", "synapse-auth-worker"):
            with self.subTest(worker_name):
                api = make_api(worker_name)

                Module({}, api)

                api.register_web_resource.assert_not_called()
