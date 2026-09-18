"""Monkey patches for OpenTracing context propagation on outbound HTTP requests."""

from __future__ import annotations

import treq as _treq_module
from twisted.web.http_headers import Headers

try:
    import opentracing
except ImportError:
    opentracing = None


def _get_inject_header_dict():
    try:
        from synapse.logging.opentracing import inject_header_dict

        return inject_header_dict
    except ImportError:
        return None


class SimpleHttpClientPatch:
    """Stub module class.

    Synapse's ``modules:`` config imports this module, which triggers the
    treq.request monkey patch below at import time. The class itself does
    nothing; its only job is giving homeserver.yaml something to reference.
    """

    def __init__(self, config: dict, api) -> None:
        pass

    @staticmethod
    def parse_config(config: dict):
        return config


_inject_header_dict = _get_inject_header_dict()

if _inject_header_dict:
    _original_treq_request = _treq_module.request

    def _patched_treq_request(method, url, **kwargs):
        """Inject the active OpenTracing span into outbound treq requests.

        Synapse's SimpleHttpClient calls treq.request directly without
        injecting trace headers. MatrixFederationHttpClient and
        ApplicationServiceApi._get_headers already inject, but SimpleHttpClient
        does not, and there's no extension point.

        Args:
            method: HTTP method (GET, POST, etc).
            url: Request URL.
            kwargs: Additional arguments passed to treq.request.

        Returns:
            Deferred from treq.request with trace headers injected.
        """
        if opentracing and opentracing.tracer.active_span:
            headers = kwargs.get("headers")
            if headers is None:
                headers = Headers()
                kwargs["headers"] = headers
            if isinstance(headers, Headers):
                _inject_header_dict(headers._rawHeaders, check_destination=False)
        return _original_treq_request(method, url, **kwargs)

    _treq_module.request = _patched_treq_request
