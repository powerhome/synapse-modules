"""Tests for module gating — the prod-safety guarantee.

The endpoint must never exist unless the module is explicitly enabled with a
secret, and never on a worker — except the receipts stream writer, which hosts
only the internal receipt endpoint the main process forwards to. These assert
that the module registers exactly the right resources in each case.
"""

import unittest
from unittest.mock import MagicMock, create_autospec

from synapse.config._base import ConfigError
from synapse.module_api import ModuleApi

from fixtures import Module


def make_api(worker_name=None):
    api = create_autospec(ModuleApi)
    api.worker_name = worker_name
    api._hs = MagicMock()
    return api


class ModuleGatingTestSuite(unittest.TestCase):
    def test_disabled_registers_nothing(self):
        api = make_api()

        Module({"enabled": False}, api)

        api.register_web_resource.assert_not_called()

    def test_absent_config_defaults_to_disabled(self):
        api = make_api()

        Module({}, api)

        api.register_web_resource.assert_not_called()

    def test_workers_register_nothing_even_when_enabled(self):
        api = make_api(worker_name="generic_worker")
        api._hs.get_instance_name.return_value = "generic_worker"
        api._hs.config.worker.writers.receipts = ["synapse-receipts-worker"]

        Module({"enabled": True, "shared_secret": "s3cret"}, api)

        api.register_web_resource.assert_not_called()

    def test_the_receipts_writer_registers_the_receipt_endpoint(self):
        api = make_api(worker_name="synapse-receipts-worker")
        api._hs.get_instance_name.return_value = "synapse-receipts-worker"
        api._hs.config.worker.writers.receipts = ["synapse-receipts-worker"]

        Module({"enabled": True, "shared_secret": "s3cret"}, api)

        api.register_web_resource.assert_called_once()
        kwargs = api.register_web_resource.call_args.kwargs
        self.assertEqual(kwargs["path"], "/_fixtures/receipt")

    def test_the_receipts_writer_registers_nothing_when_disabled(self):
        api = make_api(worker_name="synapse-receipts-worker")
        api._hs.get_instance_name.return_value = "synapse-receipts-worker"
        api._hs.config.worker.writers.receipts = ["synapse-receipts-worker"]

        Module({"enabled": False}, api)

        api.register_web_resource.assert_not_called()

    def test_enabled_without_a_secret_is_a_config_error(self):
        api = make_api()

        with self.assertRaises(ConfigError):
            Module({"enabled": True, "shared_secret": ""}, api)

    def test_enabled_with_a_secret_registers_the_endpoint(self):
        api = make_api()

        Module({"enabled": True, "shared_secret": "s3cret"}, api)

        api.register_web_resource.assert_called_once()
        kwargs = api.register_web_resource.call_args.kwargs
        self.assertEqual(kwargs["path"], "/_fixtures")


if __name__ == "__main__":
    unittest.main()
