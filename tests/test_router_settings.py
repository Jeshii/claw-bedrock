"""Tests for router settings storage and routing_strategy validation.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=test-key python3 -m pytest tests/ -v
"""

import os
import sys
import tempfile

import pytest


def _clean_import_modules():
    """Remove cached app modules so they re-initialize with fresh env vars."""
    keys = [
        k
        for k in sys.modules
        if k.startswith(("db", "management_app", "encryption_utils", "password_utils"))
    ]
    for k in keys:
        del sys.modules[k]


@pytest.fixture(autouse=True)
def test_env():
    """Each test gets an isolated CONFIG_DIR and clean module imports."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CONFIG_DIR"] = tmpdir
        # Test-only key. This file is committed and the repo is public, so never
        # reuse a deployment ENCRYPTION_KEY here — the two must stay distinct.
        os.environ["ENCRYPTION_KEY"] = "7et38Ta-TUAjQExZycUZs4X1HZq9CqfgCoVcQmJjvMs="
        os.environ["MANAGEMENT_PASSWORD"] = ""

        _clean_import_modules()

        from fastapi.testclient import TestClient

        import db as db_mod
        from management_app import app

        with TestClient(app) as client:
            yield client, tmpdir, db_mod


# ── Tests ───────────────────────────────────────────────────────────────────


class TestStrategyLiterals:
    """routing_strategy must be a literal LiteLLM's Router actually accepts."""

    @pytest.mark.parametrize(
        "legacy,canonical",
        [
            ("cost-based", "cost-based-routing"),
            ("latency-based", "latency-based-routing"),
            ("usage-based", "usage-based-routing"),
            ("shuffle", "simple-shuffle"),
            ("COST-BASED", "cost-based-routing"),
            ("  cost-based-routing  ", "cost-based-routing"),
        ],
    )
    def test_normalizes_short_forms(self, test_env, legacy, canonical):
        """The Groups page used to save short forms LiteLLM does not accept."""
        _client, _tmpdir, db = test_env

        db.set_router_settings({"routing_strategy": legacy})

        assert db.get_router_settings()["routing_strategy"] == canonical
        # The canonical value is what reaches the generated config.
        assert db.get_models_for_litellm()["router_settings"]["routing_strategy"] == (
            canonical
        )

    @pytest.mark.parametrize("bad", ["", "  ", "cheapest", "simple_shuffle", 7, None])
    def test_rejects_unknown_values_on_read(self, test_env, bad):
        """An unrecognized strategy is dropped rather than passed to LiteLLM.

        LiteLLM ignores a strategy it does not recognize, so a stale bad value
        would silently leave routing on the default with no visible sign.
        """
        _client, _tmpdir, db = test_env

        db.set_router_settings({"routing_strategy": bad, "num_retries": 2})

        settings = db.get_router_settings()
        assert "routing_strategy" not in settings
        # Unrelated settings survive the cleanup.
        assert settings["num_retries"] == 2

    def test_every_ui_offered_strategy_is_accepted(self, test_env):
        """Each literal in db.ROUTING_STRATEGIES round-trips unchanged."""
        _client, _tmpdir, db = test_env

        for strategy in db.ROUTING_STRATEGIES:
            db.set_router_settings({"routing_strategy": strategy})
            assert db.get_router_settings()["routing_strategy"] == strategy

    def test_post_saves_canonical_value(self, test_env):
        """The endpoint persists the literal, not the string it was handed."""
        client, _tmpdir, _db = test_env

        resp = client.post(
            "/api/settings/router",
            json={"routing_strategy": "cost-based", "num_retries": 3},
        )

        assert resp.status_code == 200
        assert client.get("/api/settings/router").json()["routing_strategy"] == (
            "cost-based-routing"
        )

    def test_post_rejects_unknown_strategy(self, test_env):
        """An unusable strategy is a 400, not a silently ignored setting."""
        client, _tmpdir, _db = test_env

        resp = client.post(
            "/api/settings/router", json={"routing_strategy": "cheapest-first"}
        )

        assert resp.status_code == 400
        assert "cheapest-first" in resp.json()["detail"]
        assert client.get("/api/settings/router").json().get("routing_strategy") is None

    def test_post_allows_clearing_strategy(self, test_env):
        """An empty value means 'use LiteLLM's default', so it is accepted."""
        client, _tmpdir, _db = test_env

        client.post("/api/settings/router", json={"routing_strategy": "cost-based"})
        resp = client.post("/api/settings/router", json={"routing_strategy": ""})

        assert resp.status_code == 200
        assert client.get("/api/settings/router").json() == {}

    def test_post_ignores_unrelated_keys(self, test_env):
        """Only the three known router keys are persisted."""
        client, _tmpdir, db = test_env

        resp = client.post(
            "/api/settings/router",
            json={"routing_strategy": "simple-shuffle", "redis_host": "attacker.test"},
        )

        assert resp.status_code == 200
        assert "redis_host" not in db.get_router_settings()
