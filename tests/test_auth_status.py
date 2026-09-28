"""Tests for GET /api/auth/status and the auth-error lifecycle it exposed.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=<fernet-key> python3 -m pytest tests/ -q
"""

import json
import subprocess
import sys
import tempfile
import time


def _raise_file_not_found(*args, **kwargs):
    raise FileNotFoundError(2, "No such file or directory: 'aws'")


def _clean_import_modules():
    prefixes = (
        "db",
        "management_app",
        "encryption_utils",
        "password_utils",
        "settings_resolver",
    )
    for k in [k for k in sys.modules if k.startswith(prefixes)]:
        del sys.modules[k]


import pytest


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    """Isolated CONFIG_DIR, clean modules, and a neutral token refresher."""
    for name in (
        "AWS_REGION",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("CONFIG_DIR", tmpdir)
        monkeypatch.setenv(
            "ENCRYPTION_KEY", "7et38Ta-TUAjQExZycUZs4X1HZq9CqfgCoVcQmJjvMs="
        )
        monkeypatch.setenv("MANAGEMENT_PASSWORD", "")

        _clean_import_modules()

        from fastapi.testclient import TestClient

        import db as db_mod
        import token_refresher
        from management_app import app

        refresher = token_refresher.token_refresher
        monkeypatch.setattr(refresher, "_fetched_at", 0, raising=False)
        monkeypatch.setattr(refresher, "_auth_error", None, raising=False)
        monkeypatch.setattr(refresher, "_auth_url", None, raising=False)
        monkeypatch.setattr(refresher, "_needs_login", False, raising=False)
        monkeypatch.setattr(refresher, "_awaiting_code", False, raising=False)

        with TestClient(app) as client:
            yield client, db_mod, refresher, monkeypatch


def _status(client) -> dict:
    res = client.get("/api/auth/status")
    assert res.status_code == 200
    return res.json()


class TestBackwardCompatibility:
    def test_all_legacy_keys_still_present(self, test_env):
        """auth.js and updateDashboardBanner depend on these exact keys."""
        client, _db, _r, _mp = test_env
        data = _status(client)
        for key in (
            "auth_needed",
            "auth_url",
            "awaiting_code",
            "auth_error",
            "openrouter",
        ):
            assert key in data, f"missing legacy key {key}"

    def test_openrouter_shape_unchanged(self, test_env):
        client, _db, _r, mp = test_env
        assert _status(client)["openrouter"] == {"configured": False}
        mp.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert _status(client)["openrouter"] == {"configured": True}

    def test_bedrock_block_added(self, test_env):
        client, _db, _r, _mp = test_env
        data = _status(client)
        assert set(data["bedrock"]) == {
            "region",
            "profile",
            "token",
            "credential_env",
            "warnings",
        }


class TestProvenance:
    def test_default_source_when_env_unset(self, test_env):
        client, _db, refresher, _mp = test_env
        region = _status(client)["bedrock"]["region"]
        assert region["source"] == "default"
        assert region["value"] == refresher._region

    def test_env_source_when_env_set(self, test_env):
        client, _db, _r, mp = test_env
        mp.setenv("AWS_REGION", "eu-central-1")
        region = _status(client)["bedrock"]["region"]
        assert region["source"] == "env"
        assert region["value"] == "eu-central-1"

    def test_config_source_when_only_config_set(self, test_env):
        client, db_mod, _r, _mp = test_env
        db_mod.set_setting("bedrock_region", "sa-east-1")
        region = _status(client)["bedrock"]["region"]
        assert region["source"] == "config"
        assert region["value"] == "sa-east-1"

    def test_env_shadows_config_and_says_so(self, test_env):
        client, db_mod, _r, mp = test_env
        db_mod.set_setting("bedrock_region", "sa-east-1")
        mp.setenv("AWS_REGION", "eu-central-1")
        region = _status(client)["bedrock"]["region"]
        assert region["value"] == "eu-central-1"
        assert region["shadowed"] == ["config"]

    def test_profile_provenance(self, test_env):
        client, _db, _r, mp = test_env
        mp.setenv("AWS_PROFILE", "my-sso-profile")
        profile = _status(client)["bedrock"]["profile"]
        assert profile["value"] == "my-sso-profile"
        assert profile["source"] == "env"


class TestTokenState:
    def test_absent_before_first_refresh(self, test_env):
        client, _db, _r, _mp = test_env
        token = _status(client)["bedrock"]["token"]
        assert token["present"] is False
        assert token["age_seconds"] is None
        assert token["expires_in"] is None
        assert token["stale"] is True
        assert token["ttl_seconds"] == 2700

    def test_fresh_token_reports_remaining_ttl(self, test_env):
        client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_fetched_at", time.time() - 600, raising=False)
        token = _status(client)["bedrock"]["token"]
        assert token["present"] is True
        assert token["stale"] is False
        assert 2000 < token["expires_in"] <= 2100
        assert 590 < token["age_seconds"] <= 610

    def test_token_past_ttl_is_stale(self, test_env):
        client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_fetched_at", time.time() - 3000, raising=False)
        token = _status(client)["bedrock"]["token"]
        assert token["stale"] is True
        assert token["expires_in"] == 0

    def test_expires_in_never_negative(self, test_env):
        client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_fetched_at", time.time() - 99_999, raising=False)
        assert _status(client)["bedrock"]["token"]["expires_in"] == 0


class TestAuthErrorIsNotConsumed:
    def test_two_polls_return_the_same_error(self, test_env):
        """The regression: the status endpoint used to clear the error it read,
        and auth.js polls every second, so an error could vanish unread."""
        client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_auth_error", "'aws' CLI not found", raising=False)
        first = _status(client)["auth_error"]
        second = _status(client)["auth_error"]
        assert first == "'aws' CLI not found"
        assert second == "'aws' CLI not found"

    def test_repeated_polls_do_not_deplete(self, test_env):
        client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_auth_error", "boom", raising=False)
        for _ in range(5):
            assert _status(client)["auth_error"] == "boom"

    def test_peek_does_not_clear(self, test_env):
        _client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_auth_error", "boom", raising=False)
        assert refresher.peek_auth_error() == "boom"
        assert refresher.peek_auth_error() == "boom"

    def test_no_destructive_accessor_remains(self, test_env):
        _client, _db, refresher, _mp = test_env
        assert not hasattr(refresher, "get_auth_error")


class TestAuthErrorLifecycle:
    """peek only works because the error is cleared where it becomes false."""

    def test_successful_refresh_clears_the_error(self, test_env):
        _client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_auth_error", "stale failure", raising=False)

        class _FakeSession:
            def get_credentials(self):
                return object()

        class _FakeGenerator:
            def get_token(self, credentials, region):
                return "fake-token"

        mp.setattr(refresher, "_get_valid_session", lambda: _FakeSession())
        mp.setattr(refresher, "_generator", _FakeGenerator())
        mp.setattr(refresher, "_recently_attempted", lambda: False)

        assert refresher._refresh(force=True, coalesce=False) is True
        assert refresher.peek_auth_error() is None

    def test_retry_login_clears_the_error(self, test_env):
        _client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_auth_error", "previous attempt failed", raising=False)
        mp.setattr(refresher, "_ensure_login", lambda: None)
        refresher.retry_login()
        assert refresher.peek_auth_error() is None

    def test_missing_aws_cli_sets_the_error(self, test_env):
        """The failure the error field exists for: no `aws` binary on PATH."""
        _client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_login_failed_time", 0, raising=False)
        mp.setattr(refresher, "_login_error", None, raising=False)
        mp.setattr(subprocess, "Popen", _raise_file_not_found)

        refresher._ensure_login()

        assert refresher.peek_auth_error() is not None
        assert "aws" in refresher.peek_auth_error()

    def test_launch_failure_sets_the_error(self, test_env):
        _client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_login_failed_time", 0, raising=False)
        mp.setattr(refresher, "_login_error", None, raising=False)

        def _explode(*a, **kw):
            raise RuntimeError("no fork for you")

        mp.setattr(subprocess, "Popen", _explode)

        refresher._ensure_login()

        assert "no fork for you" in refresher.peek_auth_error()


class TestCredentialWarnings:
    def test_no_warning_without_static_keys(self, test_env):
        client, _db, _r, mp = test_env
        mp.setenv("AWS_PROFILE", "my-profile")
        assert _status(client)["bedrock"]["warnings"] == []

    def test_warning_when_profile_shadows_static_keys(self, test_env):
        client, _db, _r, mp = test_env
        mp.setenv("AWS_PROFILE", "my-profile")
        mp.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        codes = _status(client)["bedrock"]["warnings"]
        assert codes == ["env_credentials_shadowed_by_profile"]

    def test_no_warning_for_keys_alone(self, test_env):
        """With no profile, boto3 does read the env keys — nothing to warn about."""
        client, _db, _r, mp = test_env
        mp.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        mp.setenv("AWS_SECRET_ACCESS_KEY", "secret-value")
        assert _status(client)["bedrock"]["warnings"] == []

    def test_blank_profile_does_not_trip_the_warning(self, test_env):
        client, _db, _r, mp = test_env
        mp.setenv("AWS_PROFILE", "   ")
        mp.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        assert _status(client)["bedrock"]["warnings"] == []

    def test_secret_values_never_appear_in_the_response(self, test_env):
        client, _db, _r, mp = test_env
        mp.setenv("AWS_PROFILE", "my-profile")
        mp.setenv("AWS_ACCESS_KEY_ID", "AKIALEAKCANARY")
        mp.setenv("AWS_SECRET_ACCESS_KEY", "secret-leak-canary")
        blob = json.dumps(_status(client))
        assert "AKIALEAKCANARY" not in blob
        assert "secret-leak-canary" not in blob

    def test_credential_env_reports_presence_only(self, test_env):
        client, _db, _r, mp = test_env
        mp.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        creds = _status(client)["bedrock"]["credential_env"]
        assert creds == {"access_key_set": True, "secret_key_set": False}


class TestSharedPayload:
    def test_management_and_refresher_payload_agree(self, test_env):
        """The proxy route and this endpoint used to build this by hand and
        disagree — one drained the error, only one reported profile."""
        client, _db, refresher, mp = test_env
        mp.setattr(refresher, "_auth_error", "shared error", raising=False)
        payload = refresher.auth_status_payload()
        data = _status(client)
        assert data["auth_error"] == payload["auth_error"]
        assert data["needs_login"] == payload["needs_login"]
        assert data["awaiting_code"] == payload["awaiting_code"]
        assert data["bedrock"]["token"] == payload["token"]

    def test_payload_is_json_serializable(self, test_env):
        _client, _db, refresher, _mp = test_env
        json.dumps(refresher.auth_status_payload())

    def test_env_token_is_not_reported_as_user_configured(self, test_env):
        """The refresher writes BEDROCK_MANTLE_API_KEY into os.environ itself,
        so resolving that name through the env layer would misattribute it as a
        user-set value."""
        client, _db, _r, mp = test_env
        mp.setenv("BEDROCK_MANTLE_API_KEY", "app-written")
        assert _status(client)["bedrock"]["region"]["source"] == "default"
