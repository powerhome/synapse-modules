"""Tests for connect/sentry's before_send filtering."""

import unittest
from unittest.mock import patch

import psycopg2

from sentry import _before_send, _sample_rate_for


def make_hint(exc):
    try:
        raise exc
    except Exception as raised:
        return {"exc_info": (type(raised), raised, raised.__traceback__)}


def get_state_for_events():
    raise KeyError("$someEventId")


def some_other_function():
    raise KeyError("unrelated")


class TestBeforeSend(unittest.TestCase):
    def test_drops_dns_resolution_failures(self):
        exc = psycopg2.OperationalError(
            'could not translate host name "db" to address: Name or service not known'
        )
        hint = make_hint(exc)

        result = _before_send({}, hint)

        self.assertIsNone(result)

    def test_keeps_other_operational_errors(self):
        exc = psycopg2.OperationalError("connection refused")
        hint = make_hint(exc)

        result = _before_send({}, hint)

        self.assertIsNotNone(result)

    def test_samples_get_state_for_events_key_errors(self):
        try:
            get_state_for_events()
        except KeyError as raised:
            hint = {"exc_info": (KeyError, raised, raised.__traceback__)}

        with patch("sentry.random.random", return_value=0.99):
            self.assertIsNone(_before_send({}, hint))

        with patch("sentry.random.random", return_value=0.0):
            self.assertIsNotNone(_before_send({}, hint))

    def test_does_not_sample_unrelated_key_errors(self):
        try:
            some_other_function()
        except KeyError as raised:
            hint = {"exc_info": (KeyError, raised, raised.__traceback__)}

        with patch("sentry.random.random", return_value=0.99):
            self.assertIsNotNone(_before_send({}, hint))


class TestSampleRateFor(unittest.TestCase):
    def test_full_rate_in_production(self):
        self.assertEqual(_sample_rate_for("production"), 1.0)

    def test_full_rate_in_streamfinancial_production(self):
        self.assertEqual(_sample_rate_for("streamfinancial-production"), 1.0)

    def test_sampled_in_staging(self):
        self.assertEqual(_sample_rate_for("staging"), 0.05)

    def test_sampled_when_unset(self):
        self.assertEqual(_sample_rate_for(None), 0.05)


if __name__ == "__main__":
    unittest.main()
