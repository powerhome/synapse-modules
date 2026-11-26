"""Custom Sentry configuration that also applies to Synapse."""

from __future__ import annotations

import logging
import os
import random
import re

import psycopg2
import sentry_sdk
from synapse.logging.context import current_context

logger = logging.getLogger(__name__)

_MATRIX_EVENT_TYPE_RE = re.compile(r"/(?:send|state)/([^/?]+)")
_PG_DNS_RE = re.compile(
    r"could not translate host name .* to address: Name or service not known"
)


def _client_platform(user_agent: str) -> str:
    ua = user_agent.lower()
    if "android" in ua:
        return "android"
    if "iphone" in ua or "ipad" in ua:
        return "ios"
    if "macintosh" in ua or "mac os x" in ua:
        return "macos"
    if "windows" in ua:
        return "windows"
    if "linux" in ua:
        return "linux"
    return "other"


def _enrich_from_logging_context(event):
    try:
        ctx = current_context()
        request = getattr(ctx, "request", None)
        if request is None:
            return

        if request.requester and "user" not in event:
            event["user"] = {"username": request.requester}

        if request.method:
            event.setdefault("tags", {})["http_method"] = request.method

        if request.url:
            match = _MATRIX_EVENT_TYPE_RE.search(request.url)
            if match:
                event.setdefault("tags", {})["matrix_event_type"] = match.group(1)

        if request.method and request.url:
            event["transaction"] = f"{request.method} {request.url}"

        request_info = event.setdefault("request", {})
        if request.url and "url" not in request_info:
            request_info["url"] = request.url
        if request.method and "method" not in request_info:
            request_info["method"] = request.method
        if request.ip_address:
            request_info.setdefault("env", {})["REMOTE_ADDR"] = request.ip_address
        if request.user_agent:
            request_info.setdefault("headers", {})["User-Agent"] = request.user_agent
            event.setdefault("tags", {})["client_platform"] = _client_platform(
                request.user_agent
            )
    except Exception:
        logger.debug(
            "Failed to enrich Sentry event from logging context", exc_info=True
        )


# get_state_for_events raises a bare KeyError keyed on an event ID when a
# room's forward extremities include an event with no resolvable state
# group -- a real state-store defect (see purge_state_group_corruption
# docs), not something this filter fixes. It is also, on its own, ~18k
# events/30d of identical noise: sample it down to keep occasional
# visibility without it dominating the quota while the underlying bug is
# tracked separately.
_STATE_FOR_EVENTS_SAMPLE_RATE = 0.05


def _is_get_state_for_events_key_error(exc_value) -> bool:
    if not isinstance(exc_value, KeyError):
        return False
    tb = exc_value.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name == "get_state_for_events":
            return True
        tb = tb.tb_next
    return False


def _before_send(event, hint):
    if "exc_info" in hint:
        exc_type, exc_value, tb = hint["exc_info"]
        if isinstance(exc_value, psycopg2.OperationalError):
            if exc_value.args and _PG_DNS_RE.match(exc_value.args[0]):
                return None

        if _is_get_state_for_events_key_error(exc_value):
            if random.random() >= _STATE_FOR_EVENTS_SAMPLE_RATE:  # noqa: S311
                return None

    _enrich_from_logging_context(event)
    return event


# Full production visibility; every other environment is sampled down so
# non-production volume cannot exhaust the org's shared event quota.
_PRODUCTION_SAMPLE_RATE = 1.0
_NON_PRODUCTION_SAMPLE_RATE = 0.05


def _sample_rate_for(environment: str | None) -> float:
    if (environment or "").endswith("production"):
        return _PRODUCTION_SAMPLE_RATE
    return _NON_PRODUCTION_SAMPLE_RATE


def init_sentry(worker: str = "main") -> None:
    sentry_sdk.init(
        before_send=_before_send,
        release=os.environ.get("SENTRY_RELEASE"),
        sample_rate=_sample_rate_for(os.environ.get("SENTRY_ENVIRONMENT")),
    )
    sentry_sdk.set_tag("app_cluster", os.environ["SENTRY_TAG_CLUSTER"])
    sentry_sdk.set_tag("app_namespace", os.environ["SENTRY_TAG_NAMESPACE"])
    sentry_sdk.set_tag("app_node", os.environ["SENTRY_TAG_NODE"])
    sentry_sdk.set_tag("service", "synapse")
    sentry_sdk.set_tag("worker", worker)


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
        init_sentry(api.worker_name or "main")

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
