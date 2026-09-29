"""Tests for the security headers middleware.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=<key> python3 -m pytest tests/ -q

Nothing asserted the Permissions-Policy header before Playground Audio Mode, so
microphone=(self) had no guard on it. That matters more than it looks: reverting
the value to microphone=() does not throw, does not fail a test, and does not
break any page. It just makes the mic button silently never work, which is
exactly the failure the header was blocking in the first place.
"""

import os
import sys
import tempfile

import pytest


@pytest.fixture(autouse=True)
def client():
    """An isolated CONFIG_DIR and clean module imports per test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CONFIG_DIR"] = tmpdir
        # Test-only key, matching the fixtures in the other test files. This
        # file is committed and the repo is public, so never reuse a deployment
        # ENCRYPTION_KEY here — the two must stay distinct.
        os.environ["ENCRYPTION_KEY"] = "7et38Ta-TUAjQExZycUZs4X1HZq9CqfgCoVcQmJjvMs="
        os.environ["MANAGEMENT_PASSWORD"] = ""

        for name in (
            "db",
            "management_app",
            "encryption_utils",
            "password_utils",
            "settings_resolver",
        ):
            for key in [k for k in sys.modules if k.startswith(name)]:
                del sys.modules[key]

        from fastapi.testclient import TestClient

        from management_app import app

        with TestClient(app) as test_client:
            yield test_client


def _policy(client) -> str:
    resp = client.get("/login")
    assert resp.status_code == 200
    return resp.headers["Permissions-Policy"]


def test_microphone_is_allowed_same_origin(client):
    """SpeechRecognition and getUserMedia are refused unless this says otherwise."""
    assert "microphone=(self)" in _policy(client)


def test_microphone_is_not_widened_beyond_self(client):
    """(self) only. A bare microphone=() breaks the feature; * hands the mic to
    every origin, which is a much larger relaxation than the feature needs."""
    policy = _policy(client)
    assert "*" not in policy.split("microphone=")[1].split(";")[0]
    assert "(self)" in policy


def test_camera_and_geolocation_stay_denied(client):
    """Audio mode has no reason to relax these, and they were deliberately off."""
    policy = _policy(client)
    assert "camera=()" in policy
    assert "geolocation=()" in policy


def test_other_security_headers_are_unaffected(client):
    """The header change must not have cost us the rest of the middleware."""
    resp = client.get("/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
