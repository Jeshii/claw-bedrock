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
        assert resp.json() == {
            "groups": [],
            "ungrouped_count": 0,
            "ungrouped_models": [],
        }

    def test_ungrouped_models_are_listed_and_sorted(self, test_env):
        """ungrouped_models backs the add-member dropdown, so names must be present."""
        client, _tmpdir, _db = test_env

        add_model(client, "zeta", model_path="bedrock/z")
        add_model(client, "alpha", model_path="bedrock/a")
        add_model(client, "mid", group="grouped", model_path="bedrock/m")

        data = client.get("/api/model-groups").json()
        assert data["ungrouped_count"] == 2
        assert data["ungrouped_models"] == ["alpha", "zeta"]

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


class TestRename:
    """POST /api/model-groups/rename."""

    def test_renames_every_member(self, test_env):
        """All members move to the new name in one call."""
        client, _tmpdir, _db = test_env

        add_model(client, "a", group="old", model_path="bedrock/a")
        add_model(client, "b", group="old", model_path="bedrock/b")
        add_model(client, "c", group="other", model_path="bedrock/c")

        resp = client.post(
            "/api/model-groups/rename", json={"from": "old", "to": "new"}
        )
        assert resp.status_code == 200
        assert resp.json()["renamed"] == 2

        data = client.get("/api/model-groups").json()
        assert get_group(data, "old") is None
        renamed = get_group(data, "new")
        assert renamed["member_count"] == 2
        assert get_member(renamed, "a") is not None
        assert get_member(renamed, "b") is not None
        # Untouched group stays put.
        assert get_group(data, "other")["member_count"] == 1

    def test_rename_is_atomic_on_collision(self, test_env):
        """A target that already exists is rejected, leaving the source intact."""
        client, _tmpdir, _db = test_env

        add_model(client, "a", group="old", model_path="bedrock/a")
        add_model(client, "b", group="new", model_path="bedrock/b")

        resp = client.post(
            "/api/model-groups/rename", json={"from": "old", "to": "new"}
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

        data = client.get("/api/model-groups").json()
        assert get_group(data, "old")["member_count"] == 1
        assert get_group(data, "new")["member_count"] == 1

    def test_rename_unknown_group_is_404(self, test_env):
        client, _tmpdir, _db = test_env

        resp = client.post(
            "/api/model-groups/rename", json={"from": "nope", "to": "new"}
        )
        assert resp.status_code == 404

    def test_rename_to_same_name_is_a_noop(self, test_env):
        client, _tmpdir, _db = test_env

        add_model(client, "a", group="same", model_path="bedrock/a")

        resp = client.post(
            "/api/model-groups/rename", json={"from": "same", "to": "same"}
        )
        assert resp.status_code == 200
        assert resp.json()["renamed"] == 0
        assert get_group(client.get("/api/model-groups").json(), "same") is not None

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "has space",
            "has/slash",
            "quo'te",
            'dou"ble',
            "tag<script>",
            "x" * 65,
            None,
        ],
    )
    def test_rejects_unusable_target_names(self, test_env, bad):
        """A group name becomes a public model_name, so it is constrained."""
        client, _tmpdir, _db = test_env

        add_model(client, "a", group="old", model_path="bedrock/a")

        resp = client.post("/api/model-groups/rename", json={"from": "old", "to": bad})
        assert resp.status_code == 400
        # Source must survive a rejected rename.
        assert get_group(client.get("/api/model-groups").json(), "old") is not None

    @pytest.mark.parametrize("ok", ["sonnet", "gpt-5.1", "a_b", "A1", "x" * 64])
    def test_accepts_conservative_names(self, test_env, ok):
        client, _tmpdir, _db = test_env

        add_model(client, "a", group="old", model_path="bedrock/a")

        resp = client.post("/api/model-groups/rename", json={"from": "old", "to": ok})
        assert resp.status_code == 200, resp.text
        assert get_group(client.get("/api/model-groups").json(), ok) is not None

    def test_rejects_empty_source(self, test_env):
        client, _tmpdir, _db = test_env

        resp = client.post("/api/model-groups/rename", json={"from": "", "to": "new"})
        assert resp.status_code == 400

    def test_trims_surrounding_whitespace(self, test_env):
        """A padded name is normalized rather than creating a near-duplicate."""
        client, _tmpdir, _db = test_env

        add_model(client, "a", group="old", model_path="bedrock/a")

        resp = client.post(
            "/api/model-groups/rename", json={"from": "old", "to": "  new  "}
        )
        assert resp.status_code == 200
        assert resp.json()["to"] == "new"
        assert get_group(client.get("/api/model-groups").json(), "new") is not None


class TestUnassign:
    """POST /api/model-groups/unassign."""

    def test_clears_group_from_members(self, test_env):
        """Members survive as models, just without the shared name."""
        client, _tmpdir, db = test_env

        add_model(client, "a", group="doomed", model_path="bedrock/a")
        add_model(client, "b", group="doomed", model_path="bedrock/b")
        add_model(client, "c", group="keep", model_path="bedrock/c")

        resp = client.post("/api/model-groups/unassign", json={"name": "doomed"})
        assert resp.status_code == 200
        assert resp.json()["cleared"] == 2

        data = client.get("/api/model-groups").json()
        assert get_group(data, "doomed") is None
        assert data["ungrouped_count"] == 2
        assert get_group(data, "keep")["member_count"] == 1

        # The models themselves are still there. a and b fall back to their
        # own model_name now that they no longer share one; c is still
        # grouped, so it stays served under the prefixed group name.
        names = {m["model_name"] for m in db.get_models_for_litellm()["model_list"]}
        assert names == {"a", "b", "claw-bedrock/keep"}

    def test_unassign_unknown_group_is_404(self, test_env):
        client, _tmpdir, _db = test_env

        resp = client.post("/api/model-groups/unassign", json={"name": "nope"})
        assert resp.status_code == 404

    def test_unassign_rejects_bad_name(self, test_env):
        client, _tmpdir, _db = test_env

        resp = client.post("/api/model-groups/unassign", json={"name": "has space"})
        assert resp.status_code == 400

    def test_rename_then_unassign_round_trip(self, test_env):
        """The two endpoints compose without leaving stray groups behind."""
        client, _tmpdir, db = test_env

        add_model(client, "a", group="one", model_path="bedrock/a")
        add_model(client, "b", group="two", model_path="bedrock/b")

        resp = client.post(
            "/api/model-groups/rename", json={"from": "two", "to": "renamed"}
        )
        assert resp.status_code == 200
        data = client.get("/api/model-groups").json()
        assert [g["name"] for g in data["groups"]] == ["one", "renamed"]
        assert get_group(data, "renamed")["member_count"] == 1

        client.post("/api/model-groups/unassign", json={"name": "renamed"})
        data = client.get("/api/model-groups").json()
        assert [g["name"] for g in data["groups"]] == ["one"]
        assert data["ungrouped_count"] == 1

        client.post("/api/model-groups/unassign", json={"name": "one"})
        data = client.get("/api/model-groups").json()
        assert data["groups"] == []
        assert data["ungrouped_count"] == 2
        # Both models fall back to their own model_name once ungrouped.
        assert len(db.get_models_for_litellm()["model_list"]) == 2

    def test_rename_will_not_merge_existing_groups(self, test_env):
        """Renaming onto an existing group is refused, not treated as a merge.

        Merging would silently repoint clients of the losing name, so the
        caller has to unassign the target group first.
        """
        client, _tmpdir, db = test_env

        add_model(client, "a", group="keep", model_path="bedrock/a")
        add_model(client, "b", group="drop", model_path="bedrock/b")

        assert (
            client.post(
                "/api/model-groups/rename", json={"from": "drop", "to": "keep"}
            ).status_code
            == 409
        )

        client.post("/api/model-groups/unassign", json={"name": "keep"})
        resp = client.post(
            "/api/model-groups/rename", json={"from": "drop", "to": "keep"}
        )
        assert resp.status_code == 200

        data = client.get("/api/model-groups").json()
        assert [g["name"] for g in data["groups"]] == ["keep"]
        assert get_group(data, "keep")["member_count"] == 1
        assert {m["model_name"] for m in db.get_all_models()} == {"a", "b"}
