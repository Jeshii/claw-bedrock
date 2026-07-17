"""Integration tests for provider persistence and runtime config propagation.

Run from project root:
    CONFIG_DIR=/tmp CONFIG_DIR=/tmp ENCRYPTION_KEY=test-key python3 -m pytest tests/ -v
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
        os.environ["ENCRYPTION_KEY"] = "XCy9PjoLjivjmp3anXK_4qTM8k6PfIzbW2rfnnbHmkA="
        os.environ["MANAGEMENT_PASSWORD"] = ""

        _clean_import_modules()

        from management_app import app
        from fastapi.testclient import TestClient

        import db as db_mod

        with TestClient(app) as client:
            yield client, tmpdir, db_mod


# ── Helpers ─────────────────────────────────────────────────────────────────

BEDROCK_DEFAULT = {
    "name": "bedrock",
    "display_name": "Bedrock (Mantle)",
    "type": "bedrock",
    "color": "#FF9900",
    "notes": "AWS Bedrock via Mantle",
    "aws_region": "us-east-1",
    "aws_access_key_env": "AWS_ACCESS_KEY_ID",
    "aws_secret_key_env": "AWS_SECRET_ACCESS_KEY",
}


def get_db_providers(db_mod) -> list[dict]:
    return [dict(p) for p in db_mod.providers_table.all()]


def get_provider_by_name(db_mod, name: str) -> dict | None:
    for p in get_db_providers(db_mod):
        if p["name"] == name:
            return p
    return None


# ── Tests ───────────────────────────────────────────────────────────────────


class TestMetadataEdits:
    """Display-only edits (name, color, notes) — no config reload needed."""

    def test_display_name_update(self, test_env):
        """Editing display_name persists and does not trigger reload."""
        client, tmpdir, db_mod = test_env

        existing = get_provider_by_name(db_mod, "bedrock")
        assert existing is not None

        resp = client.put(
            "/api/providers/bedrock",
            json={"display_name": "Bedrock-Prod", "color": "#000000"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"]["display_name"] == "Bedrock-Prod"
        assert data["provider"]["color"] == "#000000"
        assert data["runtime_changed"] is False

        updated = get_provider_by_name(db_mod, "bedrock")
        assert updated["display_name"] == "Bedrock-Prod"

    def test_color_update_isolation(self, test_env):
        """Changing only color leaves other display fields intact."""
        client, tmpdir, db_mod = test_env

        resp = client.put(
            "/api/providers/bedrock",
            json={"color": "#00FF00"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"]["color"] == "#00FF00"
        assert data["provider"]["display_name"] == "Bedrock (Mantle)"
        assert data["provider"]["aws_region"] == "us-east-1"

    def test_notes_update(self, test_env):
        """Editing notes does not change other fields."""
        client, tmpdir, db_mod = test_env

        resp = client.put(
            "/api/providers/bedrock",
            json={"notes": "Updated notes"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"]["notes"] == "Updated notes"


class TestApiKeySemantics:
    """Explicit API-key operation rules."""

    def test_api_key_omitted_retains(self, test_env):
        """When api_key is absent from body, the existing encrypted blob is kept."""
        client, tmpdir, db_mod = test_env

        raw_before = get_provider_by_name(db_mod, "bedrock")
        api_key_before = raw_before.get("api_key", None)
        assert api_key_before is None  # bedrock has no api_key by default

        resp = client.put(
            "/api/providers/bedrock",
            json={"display_name": "NoKeyChange"},
        )
        assert resp.status_code == 200
        raw_after = get_provider_by_name(db_mod, "bedrock")
        assert raw_after.get("api_key", None) == api_key_before

    def test_api_key_set(self, test_env, monkeypatch):
        """Setting a new api_key encrypts it and never returns it in API responses."""
        client, tmpdir, db_mod = test_env

        import management_app as mgmt

        monkeypatch.setattr(mgmt, "_reload_litellm_config", lambda: True)

        resp = client.put(
            "/api/providers/bedrock",
            json={"api_key": "sk-test-new-key-12345"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"].get("api_key") is None
        assert data["provider"]["has_api_key"] is True

        raw = get_provider_by_name(db_mod, "bedrock")
        encrypted = raw.get("api_key")
        assert encrypted is not None
        assert encrypted != "sk-test-new-key-12345"

        decrypted = db_mod._decrypt_sensitive_fields(raw)
        assert decrypted["api_key"] == "sk-test-new-key-12345"

    def test_api_key_clear(self, test_env, monkeypatch):
        """clear_api_key: true removes the key value; response shows api_key=None."""
        client, tmpdir, db_mod = test_env

        import management_app as mgmt

        monkeypatch.setattr(mgmt, "_reload_litellm_config", lambda: True)

        client.put(
            "/api/providers/bedrock",
            json={"api_key": "sk-temp-key"},
        )

        resp = client.put(
            "/api/providers/bedrock",
            json={"clear_api_key": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"].get("api_key") is None
        # has_api_key remains True because bedrock still has aws_access_key_env / aws_secret_key_env

        raw = get_provider_by_name(db_mod, "bedrock")
        assert raw.get("api_key") is None

    def test_api_key_empty_body_not_cleared(self, test_env):
        """An empty string api_key is treated as no-change, not a clear."""
        client, tmpdir, db_mod = test_env

        client.put(
            "/api/providers/bedrock",
            json={"api_key": "sk-keep-me"},
        )
        raw_before = get_provider_by_name(db_mod, "bedrock")
        old_encrypted = raw_before["api_key"]

        resp = client.put(
            "/api/providers/bedrock",
            json={"api_key": ""},
        )
        assert resp.status_code == 200

        raw_after = get_provider_by_name(db_mod, "bedrock")
        assert raw_after.get("api_key") == old_encrypted


class TestRuntimeFieldEdits:
    """Runtime-affecting edits that trigger config reload."""

    def test_edit_api_base_success(self, test_env, monkeypatch):
        """Editing api_base triggers config merge and reload; values persist."""
        client, tmpdir, db_mod = test_env

        # Create an openai-compatible provider first
        client.post(
            "/api/providers",
            json={
                "name": "my-openai",
                "type": "openai-compatible",
                "api_base": "http://old-host:8000/v1",
                "api_key": "sk-old-key",
            },
        )

        # Mock LiteLLM reload to succeed
        import requests as req_mod

        class MockOK:
            status_code = 200
            ok = True

            def json(self):
                return {}

        monkeypatch.setattr(req_mod, "post", lambda *a, **kw: MockOK())

        resp = client.put(
            "/api/providers/my-openai",
            json={"api_base": "http://new-host:8080/v2"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"]["api_base"] == "http://new-host:8080/v2"
        assert data["runtime_changed"] is True

        raw = get_provider_by_name(db_mod, "my-openai")
        assert raw["api_base"] == "http://new-host:8080/v2"

    def test_reload_failure_returns_error_no_rollback(self, test_env):
        """When LiteLLM reload fails, 503 returned with structured detail; DB is NOT rolled back."""
        client, tmpdir, db_mod = test_env

        client.post(
            "/api/providers",
            json={
                "name": "norb-test",
                "type": "openai-compatible",
                "api_base": "http://stable-host:8000/v1",
            },
        )

        resp = client.put(
            "/api/providers/norb-test",
            json={"api_base": "http://fail-host:9999/v1"},
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["saved"] is True
        assert detail["applied"] is False
        assert detail["stage"] == "reload"

        raw = get_provider_by_name(db_mod, "norb-test")
        assert raw["api_base"] == "http://fail-host:9999/v1"

    def test_edit_aws_region(self, test_env, monkeypatch):
        """Editing a Bedrock provider's region is persisted."""
        client, tmpdir, db_mod = test_env

        import requests as req_mod

        class MockOK:
            status_code = 200
            ok = True

            def json(self):
                return {}

        monkeypatch.setattr(req_mod, "post", lambda *a, **kw: MockOK())

        resp = client.put(
            "/api/providers/bedrock",
            json={"aws_region": "eu-west-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"]["aws_region"] == "eu-west-1"
        assert data["runtime_changed"] is True

        raw = get_provider_by_name(db_mod, "bedrock")
        assert raw["aws_region"] == "eu-west-1"


class TestConfigMerge:
    """Provider defaults should appear in generated LiteLLM config."""

    def test_provider_defaults_in_config(self, test_env):
        """Provider api_base should be injected into model litellm_params."""
        client, tmpdir, db_mod = test_env

        client.post(
            "/api/providers",
            json={
                "name": "test-ollama",
                "type": "openai-compatible",
                "api_base": "http://ollama:11434/v1",
            },
        )

        client.post(
            "/api/models",
            json={
                "model_name": "test-llama",
                "litellm_params": {
                    "model": "ollama/llama3.2",
                },
                "provider": "test-ollama",
            },
        )

        merged = db_mod.get_models_for_litellm()
        model_entry = None
        for m in merged["model_list"]:
            if m.get("model_name") == "test-llama":
                model_entry = m
                break

        assert model_entry is not None, (
            f"Model not found in {[x['model_name'] for x in merged['model_list']]}"
        )
        lp = model_entry.get("litellm_params", {})
        assert lp.get("api_base") == "http://ollama:11434/v1"

    def test_model_explicit_overrides_provider(self, test_env):
        """Model-level litellm_params take precedence over provider defaults."""
        client, tmpdir, db_mod = test_env

        client.post(
            "/api/providers",
            json={
                "name": "override-test",
                "type": "openai-compatible",
                "api_base": "http://provider:8000/v1",
            },
        )

        client.post(
            "/api/models",
            json={
                "model_name": "override-model",
                "litellm_params": {
                    "model": "gpt-4",
                    "api_base": "http://model-specific:9000/v1",
                },
                "provider": "override-test",
            },
        )

        merged = db_mod.get_models_for_litellm()
        model_entry = None
        for m in merged["model_list"]:
            if m.get("model_name") == "override-model":
                model_entry = m
                break

        assert model_entry is not None, (
            f"Model not found in {[x['model_name'] for x in merged['model_list']]}"
        )
        assert (
            model_entry["litellm_params"]["api_base"] == "http://model-specific:9000/v1"
        )

    def test_missing_provider_skips_model_and_does_not_abort(self, test_env, caplog):
        """A model referencing a nonexistent provider should be skipped, not crash."""
        import logging

        client, tmpdir, db_mod = test_env

        # Add a valid model that should appear
        client.post(
            "/api/models",
            json={
                "model_name": "valid-model",
                "litellm_params": {"model": "gpt-4"},
                "provider": "bedrock",
            },
        )

        # Add an orphan model with a nonexistent provider
        client.post(
            "/api/models",
            json={
                "model_name": "orphan-model",
                "litellm_params": {"model": "gpt-4"},
                "provider": "nonexistent-provider",
            },
        )

        with caplog.at_level(logging.WARNING):
            config = db_mod.get_models_for_litellm()

        names = {item["model_name"] for item in config["model_list"]}
        assert "orphan-model" not in names
        assert "valid-model" in names
        assert "referenced provider" in caplog.text


class TestSanitization:
    """API responses must never leak secret values."""

    SENSITIVE_PATTERNS = ["api_key", "secret_key", "aws_secret_key_env"]

    def _check_no_secrets(self, obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if any(p in k.lower() for p in self.SENSITIVE_PATTERNS):
                    if isinstance(v, str) and len(v) > 0:
                        if k == "has_api_key":
                            continue
                        if k == "aws_secret_key_env":
                            continue
                        pytest.fail(f"Secret leaked at {path}.{k}={v!r}")
                self._check_no_secrets(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._check_no_secrets(item, f"{path}[{i}]")

    def test_list_providers_no_secrets(self, test_env):
        client, tmpdir, db_mod = test_env
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        body = resp.json()
        for p in body.get("providers", []):
            assert p.get("api_key") is None

    def test_get_provider_no_secrets(self, test_env):
        client, tmpdir, db_mod = test_env
        resp = client.get("/api/providers/bedrock")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"].get("api_key") is None
        assert body["provider"].get("has_api_key") is not None


class TestValidation:
    """Input validation and error handling."""

    def test_rename_rejected_via_put(self, test_env):
        """PUT with a different name in body should be rejected."""
        client, tmpdir, db_mod = test_env
        resp = client.put(
            "/api/providers/bedrock",
            json={"name": "new-name"},
        )
        assert resp.status_code == 400
        assert "not supported" in resp.json()["detail"].lower()

    def test_unknown_provider_404(self, test_env):
        client, tmpdir, db_mod = test_env
        resp = client.put(
            "/api/providers/nonexistent",
            json={"display_name": "Ghost"},
        )
        assert resp.status_code == 404

    def test_duplicate_provider_409(self, test_env):
        client, tmpdir, db_mod = test_env
        resp = client.post(
            "/api/providers", json={"name": "bedrock", "type": "bedrock"}
        )
        assert resp.status_code == 409


class TestPutFlow:
    """End-to-end PUT flow: validate → persist → regenerate → reload → return DTO."""

    def _mock_ok_reload(self, monkeypatch):
        import requests as req_mod

        class MockOK:
            status_code = 200
            ok = True

            def json(self):
                return {}

        monkeypatch.setattr(req_mod, "post", lambda *a, **kw: MockOK())

    def test_edit_api_base_and_key_persists(self, test_env, monkeypatch):
        """PUT new api_base and api_key, GET back confirming both stick."""
        client, tmpdir, db_mod = test_env

        import management_app as mgmt

        monkeypatch.setattr(mgmt, "_reload_litellm_config", lambda: True)

        client.post(
            "/api/providers",
            json={
                "name": "test-prov",
                "type": "openai-compatible",
                "api_base": "http://old:8000/v1",
            },
        )
        NEW_BASE = "http://new-host:9090/v2"
        NEW_KEY = "sk-very-secret-abc"

        resp = client.put(
            "/api/providers/test-prov",
            json={"api_base": NEW_BASE, "api_key": NEW_KEY},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"]["api_base"] == NEW_BASE
        assert data["provider"].get("api_key") is None
        assert data["provider"]["has_api_key"] is True

        # Raw DB: key is encrypted, never plaintext
        raw = get_provider_by_name(db_mod, "test-prov")
        assert raw["api_base"] == NEW_BASE
        assert raw.get("api_key") != NEW_KEY
        assert raw.get("api_key") is not None

        # Sanitized GET also confirms
        get_resp = client.get("/api/providers/test-prov")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["provider"]["api_base"] == NEW_BASE
        assert body["provider"].get("api_key") is None
        assert body["provider"]["has_api_key"] is True

    def test_updated_provider_in_generated_config(self, test_env):
        """After PUT, the generated LiteLLM config reflects the updated values."""
        client, tmpdir, db_mod = test_env
        client.post(
            "/api/providers",
            json={
                "name": "cfg-prov",
                "type": "openai-compatible",
                "api_base": "http://old:8000/v1",
                "api_key": "sk-old-key",
            },
        )
        client.post(
            "/api/models",
            json={
                "model_name": "cfg-model",
                "litellm_params": {"model": "openai/gpt-4"},
                "provider": "cfg-prov",
            },
        )

        NEW_BASE = "http://new:8080/v2"
        client.put(
            "/api/providers/cfg-prov",
            json={"api_base": NEW_BASE},
        )

        merged = db_mod.get_models_for_litellm()
        entry = next(
            (
                m
                for m in merged["model_list"]
                if m.get("model_name", "").endswith("cfg-model")
            ),
            None,
        )
        assert entry is not None
        assert entry["litellm_params"]["api_base"] == NEW_BASE
        assert "api_key" in entry["litellm_params"]

    def test_values_survive_reload(self, test_env, monkeypatch):
        """After a successful reload, GET still shows the updated values."""
        client, tmpdir, db_mod = test_env
        client.post(
            "/api/providers",
            json={
                "name": "survive-prov",
                "type": "openai-compatible",
                "api_base": "http://before:8000/v1",
            },
        )

        self._mock_ok_reload(monkeypatch)
        NEW_BASE = "http://after:8080/v2"
        resp = client.put(
            "/api/providers/survive-prov",
            json={"api_base": NEW_BASE},
        )
        assert resp.status_code == 200
        assert resp.json()["provider"]["api_base"] == NEW_BASE

        get_resp = client.get("/api/providers/survive-prov")
        assert get_resp.status_code == 200
        assert get_resp.json()["provider"]["api_base"] == NEW_BASE

    def test_config_merge_failure_returns_error(self, test_env, monkeypatch):
        """When config generation fails, API returns 503 with structured detail and no rollback."""
        client, tmpdir, db_mod = test_env
        client.post(
            "/api/providers",
            json={
                "name": "merge-fail",
                "type": "openai-compatible",
                "api_base": "http://stable:8000/v1",
            },
        )

        import management_app as mgmt

        monkeypatch.setattr(
            mgmt, "merge_configs_atomic", lambda: (False, "simulated-merge-error")
        )

        resp = client.put(
            "/api/providers/merge-fail",
            json={"api_base": "http://boom:9999/v1"},
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["saved"] is True
        assert detail["applied"] is False
        assert detail["stage"] == "merge"

        raw = get_provider_by_name(db_mod, "merge-fail")
        assert raw["api_base"] == "http://boom:9999/v1"

    def test_reload_failure_returns_error(self, test_env, monkeypatch):
        """When LiteLLM reload fails, API returns 503 with structured detail and no rollback."""
        client, tmpdir, db_mod = test_env
        client.post(
            "/api/providers",
            json={
                "name": "reload-fail",
                "type": "openai-compatible",
                "api_base": "http://stable:8000/v1",
            },
        )

        import management_app as mgmt

        monkeypatch.setattr(mgmt, "_reload_litellm_config", lambda: False)

        resp = client.put(
            "/api/providers/reload-fail",
            json={"api_base": "http://crash:9999/v1"},
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["saved"] is True
        assert detail["applied"] is False
        assert detail["stage"] == "reload"

        raw = get_provider_by_name(db_mod, "reload-fail")
        assert raw["api_base"] == "http://crash:9999/v1"

    def test_no_runtime_change_skips_regenerate_and_reload(self, test_env, monkeypatch):
        """A display-only PUT must not call merge_configs_atomic or _reload_litellm_config."""
        client, tmpdir, db_mod = test_env
        client.post(
            "/api/providers",
            json={
                "name": "meta-only",
                "type": "openai-compatible",
                "api_base": "http://meta:8000/v1",
            },
        )

        import management_app as mgmt

        call_log = {"merge": 0, "reload": 0}
        original_merge = mgmt.merge_configs_atomic
        original_reload = mgmt._reload_litellm_config

        def tracking_merge():
            call_log["merge"] += 1
            return original_merge()

        def tracking_reload():
            call_log["reload"] += 1
            return original_reload()

        monkeypatch.setattr(mgmt, "merge_configs_atomic", tracking_merge)
        monkeypatch.setattr(mgmt, "_reload_litellm_config", tracking_reload)

        resp = client.put(
            "/api/providers/meta-only",
            json={"display_name": "Just Metadata", "color": "#123456"},
        )
        assert resp.status_code == 200
        assert resp.json()["runtime_changed"] is False
        assert call_log["merge"] == 0
        assert call_log["reload"] == 0

        raw = get_provider_by_name(db_mod, "meta-only")
        assert raw["display_name"] == "Just Metadata"
        assert raw["color"] == "#123456"
        assert raw["api_base"] == "http://meta:8000/v1"

    def test_unknown_field_rejected_without_write(self, test_env):
        """PUT with an unrecognised field must return 400 and leave the provider unchanged."""
        client, tmpdir, db_mod = test_env

        original = get_provider_by_name(db_mod, "bedrock")

        resp = client.put(
            "/api/providers/bedrock",
            json={"unknown_field": "whatever"},
        )
        assert resp.status_code == 400

        unchanged = get_provider_by_name(db_mod, "bedrock")
        assert unchanged == original
