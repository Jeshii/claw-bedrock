"""Tests for the Groups dashboard aggregation endpoint.

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
        os.environ["ENCRYPTION_KEY"] = "XCy9PjoLjivjmp3anXK_4qTM8k6PfIzbW2rfnnbHmkA="
        os.environ["MANAGEMENT_PASSWORD"] = ""

        _clean_import_modules()

        from fastapi.testclient import TestClient

        import db as db_mod
        from management_app import app

        with TestClient(app) as client:
            yield client, tmpdir, db_mod


# ── Helpers ─────────────────────────────────────────────────────────────────


def add_model(client, name, provider="bedrock", group=None, model_path=None):
    """Create a model. Pass model_path=None to omit litellm_params entirely."""
    body = {"model_name": name, "provider": provider}
    if group is not None:
        body["model_group"] = group
    if model_path is not None:
        body["litellm_params"] = {"model": model_path, "context_length": 200000}
    return client.post("/api/models", json=body)


def get_group(data, name):
    for g in data["groups"]:
        if g["name"] == name:
            return g
    return None


def get_member(group, model_name):
    for m in group["members"]:
        if m["model_name"] == model_name:
            return m
    return None


# ── Tests ───────────────────────────────────────────────────────────────────


class TestAggregation:
    """Grouping and ungrouped bookkeeping."""

    def test_empty_db(self, test_env):
        """No models yields an empty group list, not an error."""
        client, _tmpdir, _db = test_env

        resp = client.get("/api/model-groups")
        assert resp.status_code == 200
        assert resp.json() == {"groups": [], "ungrouped_count": 0}

    def test_grouped_and_ungrouped_split(self, test_env):
        """Grouped models bucket by name; the rest count as ungrouped."""
        client, _tmpdir, _db = test_env

        add_model(client, "sonnet-v2", group="sonnet", model_path="bedrock/a")
        add_model(client, "sonnet-v1", group="sonnet", model_path="bedrock/b")
        add_model(client, "haiku", group="haiku", model_path="bedrock/c")
        add_model(client, "loose-model", model_path="bedrock/d")

        data = client.get("/api/model-groups").json()

        assert [g["name"] for g in data["groups"]] == ["haiku", "sonnet"]
        assert data["ungrouped_count"] == 1
        assert get_group(data, "sonnet")["member_count"] == 2

    def test_groups_sorted_by_name(self, test_env):
        """Groups come back alphabetically for stable rendering."""
        client, _tmpdir, _db = test_env

        for name in ("zulu", "alpha", "mike"):
            add_model(client, f"{name}-model", group=name, model_path="bedrock/x")

        data = client.get("/api/model-groups").json()
        assert [g["name"] for g in data["groups"]] == ["alpha", "mike", "zulu"]

    def test_members_carry_provider_display_info(self, test_env):
        """Members are enriched so the UI can render provider badges."""
        client, _tmpdir, _db = test_env

        add_model(client, "sonnet-v2", group="sonnet", model_path="bedrock/a")

        member = get_member(
            get_group(client.get("/api/model-groups").json(), "sonnet"), "sonnet-v2"
        )
        assert member["_provider"]["display_name"] == "Bedrock (Mantle)"
        assert member["_provider"]["color"] == "#FF9900"

    def test_no_secrets_leak_into_members(self, test_env):
        """Provider secrets are sanitized before reaching the dashboard."""
        client, _tmpdir, _db = test_env

        client.post(
            "/api/providers",
            json={
                "name": "openai-ish",
                "type": "openai-compatible",
                "api_base": "http://example.test/v1",
                "api_key": "sk-super-secret-value",
            },
        )
        add_model(client, "gpt", provider="openai-ish", group="gpt", model_path="gpt-4")

        member = get_member(
            get_group(client.get("/api/model-groups").json(), "gpt"), "gpt"
        )
        assert member["_provider"]["api_key"] is None
        assert member["_provider"]["has_api_key"] is True
        assert "sk-super-secret-value" not in str(member)


class TestMemberStatus:
    """Config-derived status signals."""

    def test_healthy_member(self, test_env):
        """A fully configured model with a live provider reports ok."""
        client, _tmpdir, _db = test_env

        add_model(client, "sonnet-v2", group="sonnet", model_path="bedrock/a")

        member = get_member(
            get_group(client.get("/api/model-groups").json(), "sonnet"), "sonnet-v2"
        )
        assert member["status"] == {"level": "ok", "detail": ""}

    def test_dangling_provider_is_error(self, test_env):
        """A model pointing at a deleted provider is flagged and excluded."""
        client, _tmpdir, _db = test_env

        add_model(client, "orphan", group="sonnet", model_path="bedrock/a")
        client.delete("/api/providers/bedrock")

        group = get_group(client.get("/api/model-groups").json(), "sonnet")
        member = get_member(group, "orphan")

        assert member["status"]["level"] == "error"
        assert "no longer exists" in member["status"]["detail"]
        assert group["active_member_count"] == 0
        assert group["member_count"] == 1

    def test_missing_provider_assignment_is_error(self, test_env):
        """A model with no provider cannot reach LiteLLM, so it is an error."""
        client, _tmpdir, _db = test_env

        client.post(
            "/api/models",
            json={"model_name": "orphan", "litellm_params": {"model": "bedrock/a"}},
        )
        # Assign it to a group after creation, as the UI does.
        client.patch(
            "/api/models/b3JwaGFu",
            json={"model_group": "sonnet"},
        )

        data = client.get("/api/model-groups").json()
        member = get_member(get_group(data, "sonnet"), "orphan")
        assert member["status"]["level"] == "error"
        assert member["status"]["detail"] == "No provider assigned"

    def test_empty_litellm_params_is_warn(self, test_env):
        """Empty litellm_params is a warning, not an error."""
        client, _tmpdir, _db = test_env

        add_model(client, "bare", group="sonnet", model_path="")

        data = client.get("/api/model-groups").json()
        group = get_group(data, "sonnet")
        member = get_member(group, "bare")

        assert member["status"]["level"] == "warn"
        assert group["active_member_count"] == 1

    def test_warn_member_still_counts_as_active(self, test_env):
        """warn members stay in the config, so they count as active."""
        client, _tmpdir, _db = test_env

        add_model(client, "bare", group="sonnet", model_path="")
        add_model(client, "good", group="sonnet", model_path="bedrock/a")

        group = get_group(client.get("/api/model-groups").json(), "sonnet")
        assert group["active_member_count"] == 2
        assert group["member_count"] == 2

    def test_active_count_reflects_config_survival(self, test_env):
        """active_member_count matches what get_models_for_litellm emits."""
        client, _tmpdir, db = test_env

        add_model(client, "good", group="sonnet", model_path="bedrock/a")
        add_model(client, "orphan", group="sonnet", model_path="bedrock/b")
        client.delete("/api/providers/bedrock")

        group = get_group(client.get("/api/model-groups").json(), "sonnet")
        emitted = db.get_models_for_litellm()["model_list"]

        # Both models are in the group, none survive config generation.
        assert group["member_count"] == 2
        assert group["active_member_count"] == 0
        assert emitted == []
