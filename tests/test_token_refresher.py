"""Tests for the token refresher's expiry detection and refresh coalescing.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=test-key python3 -m pytest tests/ -v
"""

import asyncio
import os
import threading
import time

import pytest

import token_refresher as tr


def _bare_refresher():
    """A BedrockTokenRefresher with no side effects.

    __init__ calls _refresh (which builds a boto3 Session) and registers routes
    on litellm's app. Neither belongs in a unit test, so allocate the instance
    without running __init__ and set only the state these tests exercise.
    """
    r = object.__new__(tr.BedrockTokenRefresher)
    r._fetched_at = 0.0
    r._force_refresh = False
    r._refresh_lock = threading.Lock()
    r._last_refresh_attempt = 0.0
    r._needs_login = False
    r._auth_url = None
    r._profile = "test-profile"
    r._region = "ap-northeast-1"
    r._generator = None
    r._access_key_id = ""
    r._secret_access_key = ""
    r._auth_error = None
    return r


# --- _build_session: the profile_name pivot ----------------------------


class TestBuildSession:
    """`profile_name` decides whether env credentials are readable at all.

    botocore sets `disable_env_vars` whenever a profile is passed explicitly
    (`credentials.py:95`) and then removes the EnvProvider from the chain, so
    passing a profile made AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
    set-but-inert on every install. Omitting the profile is the only way to
    reach them, and boto3.Session only does that when profile_name is None
    (`session.py:81-82`).
    """

    def test_profile_passed_when_no_key_pair(self, monkeypatch):
        r = _bare_refresher()
        captured = {}

        def fake_session(**kwargs):
            captured.update(kwargs)
            return "session"

        monkeypatch.setattr(tr.boto3, "Session", fake_session)
        r._build_session()

        assert captured["profile_name"] == "test-profile"
        assert captured["region_name"] == "ap-northeast-1"
        assert "aws_access_key_id" not in captured

    def test_profile_omitted_when_key_pair_configured(self, monkeypatch):
        """The regression this fixes: keys passed explicitly, profile omitted."""
        r = _bare_refresher()
        r._access_key_id = "AKIAEXAMPLE"
        r._secret_access_key = "wJalrEXAMPLE"
        captured = {}

        def fake_session(**kwargs):
            captured.update(kwargs)
            return "session"

        monkeypatch.setattr(tr.boto3, "Session", fake_session)
        r._build_session()

        assert captured["aws_access_key_id"] == "AKIAEXAMPLE"
        assert captured["aws_secret_access_key"] == "wJalrEXAMPLE"
        # The kwarg must be absent. boto3.Session treats an explicit None and an
        # omitted argument the same (`session.py:81-82` only calls
        # set_config_variable when profile_name is not None), but asserting
        # absence is what pins the behaviour: a future edit that passes
        # `profile_name=self._profile` unconditionally is the bug returning.
        assert "profile_name" not in captured

    def test_half_a_key_pair_still_uses_the_profile(self, monkeypatch):
        """One half cannot authenticate, so it must not displace the profile."""
        r = _bare_refresher()
        r._access_key_id = "AKIAEXAMPLE"
        captured = {}

        monkeypatch.setattr(
            tr.boto3, "Session", lambda **kw: captured.update(kw) or "session"
        )
        r._build_session()

        assert captured["profile_name"] == "test-profile"


# --- configure() -------------------------------------------------------


class TestConfigure:
    """configure() is what makes the settings card live without a restart."""

    def test_rebuilds_session_on_region_change(self, monkeypatch):
        """The new region must reach the session that mints the token.

        configure() delegates to _refresh, which calls _build_session; this
        exercises the whole chain with only the token generator stubbed, because
        the link worth pinning is region -> session, not that _refresh runs.
        """
        r = _bare_refresher()
        sessions = []

        class FakeSession:
            def __init__(self):
                sessions.append(r._region)

            def get_credentials(self):
                return object()

        monkeypatch.setattr(tr.boto3, "Session", lambda **kw: FakeSession())
        monkeypatch.setattr(
            r, "_generator", type("G", (), {"get_token": lambda *a: "tok"})()
        )

        assert r.configure(region="eu-west-1") is True
        assert r._region == "eu-west-1"
        assert sessions == ["eu-west-1"]
        assert os.environ["BEDROCK_MANTLE_API_KEY"] == "tok"

    def test_drops_stale_token_before_refresh(self, monkeypatch):
        """A failed refresh must not leave the previous token looking current."""
        r = _bare_refresher()
        r._fetched_at = time.time()
        r._needs_login = True
        r._auth_error = "stale failure"
        monkeypatch.setattr(r, "_refresh", lambda **kw: False)

        assert r.configure(region="eu-west-1") is False
        assert r._fetched_at == 0
        assert r._needs_login is False
        assert r._auth_error is None

    def test_forced_refresh_bypasses_coalescing(self, monkeypatch):
        """A deliberate reconfigure must not be absorbed by a recent attempt."""
        r = _bare_refresher()
        r._last_refresh_attempt = time.time()
        seen = {}

        def fake_refresh(force=False, coalesce=True):
            seen["force"] = force
            seen["coalesce"] = coalesce
            return True

        monkeypatch.setattr(r, "_refresh", fake_refresh)
        r.configure(region="eu-west-1")

        assert seen == {"force": True, "coalesce": False}

    def test_no_change_skips_refresh(self, monkeypatch):
        r = _bare_refresher()
        r._fetched_at = time.time()
        monkeypatch.setattr(
            r, "_refresh", lambda **kw: pytest.fail("refresh should be skipped")
        )

        assert r.configure(region=r._region, profile=r._profile) is True

    def test_clearing_key_pair_returns_to_profile(self, monkeypatch):
        """Clearing the pair must hand control back to the SSO profile."""
        r = _bare_refresher()
        r._access_key_id = "AKIAEXAMPLE"
        r._secret_access_key = "wJalrEXAMPLE"
        captured = {}

        def fake_session(**kwargs):
            captured.update(kwargs)
            return _FakeSession()

        monkeypatch.setattr(tr.boto3, "Session", fake_session)
        monkeypatch.setattr(
            r, "_generator", type("G", (), {"get_token": lambda *a: "tok"})()
        )

        assert r.configure(access_key_id="", secret_access_key="") is True
        assert r._access_key_id == ""
        assert captured["profile_name"] == r._profile


class _FakeSession:
    def get_credentials(self):
        return object()


# --- _is_expired_error -------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "litellm.AuthenticationError: OpenAIException - API key expired.",
        "ExpiredToken: The security token included in the request is invalid",
        "invalid_api_key",
        "InvalidToken",
        "UnrecognizedClientException: The security token included in the request is invalid",
        "The request signature has expired",
        "credentials expired",
        "The session has expired, please re-authenticate with the CLI",
    ],
)
def test_recognises_real_auth_failures(message):
    assert _bare_refresher()._is_expired_error(Exception(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        # The failure that motivated this: a stale model id reads as
        # "does not exist", and a refresh must not be attempted for it.
        (
            'BedrockException - {"code":"not_found_error","message":"The model '
            "'openai.gpt-6-luna' does not exist\"}"
        ),
        "certificate expired",
        "Connection error.",
        "Rate limit exceeded, retry later.",
    ],
)
def test_ignores_non_auth_errors(message):
    assert _bare_refresher()._is_expired_error(Exception(message)) is False


def test_bare_expired_no_longer_matches():
    """A lone 'expired' must not be enough — that was the original bug."""
    r = _bare_refresher()
    assert r._is_expired_error(Exception("Something has expired")) is False


# --- _refresh coalescing ----------------------------------------------


def test_refresh_runs_once_for_a_burst(monkeypatch):
    """N concurrent failures cost one token fetch, not N."""
    r = _bare_refresher()
    calls = []

    def fake_locked():
        calls.append(time.time())
        return True

    monkeypatch.setattr(r, "_refresh_locked", fake_locked)

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(r._refresh(force=True)))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, f"expected 1 token fetch, got {len(calls)}"
    assert results.count(True) == 1


def test_coalesced_call_reports_no_fetch():
    r = _bare_refresher()
    calls = []

    def fake_locked():
        calls.append(1)
        return True

    r._refresh_locked = fake_locked
    assert r._refresh(force=True) is True
    # A second forced refresh inside the window is satisfied by the first.
    assert r._refresh(force=True) is False
    assert len(calls) == 1


def test_coalesce_false_always_refreshes():
    """Post-login refresh must run even if an attempt just failed."""
    r = _bare_refresher()
    calls = []

    def fake_locked():
        calls.append(1)
        return True

    r._refresh_locked = fake_locked
    r._refresh(force=True)
    r._refresh(force=True, coalesce=False)
    assert len(calls) == 2


def test_ttl_skips_unforced_refresh():
    r = _bare_refresher()
    r._fetched_at = time.time()
    calls = []
    r._refresh_locked = lambda: calls.append(1)
    assert r._refresh() is False
    assert calls == []


def test_expired_ttl_forces_refresh():
    r = _bare_refresher()
    r._fetched_at = time.time() - r.TOKEN_TTL - 1
    calls = []
    r._refresh_locked = lambda: (calls.append(1), True)[1]
    assert r._refresh() is True
    assert len(calls) == 1


def test_failed_attempt_is_still_coalesced():
    """A failed attempt throttles retries instead of hammering the auth path."""
    r = _bare_refresher()
    calls = []
    r._refresh_locked = lambda: (calls.append(1), False)[1]
    assert r._refresh(force=True) is False
    assert r._refresh(force=True) is False
    assert len(calls) == 1


# --- failure hook -----------------------------------------------------


def test_failure_hook_only_flags_and_does_not_refresh():
    """The hook must not refresh inline; that blocked the event loop."""
    r = _bare_refresher()
    calls = []
    r._refresh_locked = lambda: calls.append(1)
    r._force_refresh = False

    asyncio.run(
        r.async_log_failure_event(
            kwargs={"exception": Exception("API key expired")},
            response_obj=None,
            start_time=0,
            end_time=0,
        )
    )

    assert r._force_refresh is True, "must arm the pre-call hook"
    assert calls == [], "must not perform the refresh inline"


def test_failure_hook_ignores_non_auth_errors():
    r = _bare_refresher()
    r._force_refresh = False
    asyncio.run(
        r.async_log_failure_event(
            kwargs={"exception": Exception("The model 'x' does not exist")},
            response_obj=None,
            start_time=0,
            end_time=0,
        )
    )
    assert r._force_refresh is False
