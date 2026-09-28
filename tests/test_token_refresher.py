"""Tests for the token refresher's expiry detection and refresh coalescing.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=test-key python3 -m pytest tests/ -v
"""

import asyncio
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
    return r


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
