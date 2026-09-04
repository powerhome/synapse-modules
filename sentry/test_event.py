"""Test script for Sentry integration."""
import sentry_sdk

from connect.sentry import init_sentry

init_sentry()
sentry_sdk.capture_message("Sentry test: synapse")
sentry_sdk.flush()
