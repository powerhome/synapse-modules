"""Custom Sentry configuration that also applies to Synapse."""

import os
import re

import psycopg2
import sentry_sdk


class SentryInitializer:
    """A module that initializes Sentry."""

    def __init__(self, config: dict, api) -> None:
        """Initialize a new instance.

        Args:
            config (dict):
                The values obtained from `homeserver.yaml` for this module.
            api:
                An instance of `synapse.module_api.ModuleApi`
                that enables this module to communicate with Synapse.
        """

        def before_send(event, hint):
            if "exc_info" in hint:
                exc_type, exc_value, tb = hint["exc_info"]
                if isinstance(exc_value, psycopg2.OperationalError):
                    pattern = r"""could not translate host name .* to address: Name or service not known"""  # noqa: E501
                    if exc_value.args and re.match(pattern, exc_value.args[0]):
                        return None
            return event

        sentry_sdk.init(
            before_send=before_send,
        )

        sentry_sdk.set_tag("app_cluster", os.environ["SENTRY_TAG_CLUSTER"])
        sentry_sdk.set_tag("app_namespace", os.environ["SENTRY_TAG_NAMESPACE"])
        sentry_sdk.set_tag("app_node", os.environ["SENTRY_TAG_NODE"])

    @staticmethod
    def parse_config(config: dict):
        """Perform post-processing on `homeserver.yaml` configuration.

        Args:
            config (dict):
                The values obtained from `homeserver.yaml` for this module.

        Returns:
            The post-processed configuration.
        """
        return config
