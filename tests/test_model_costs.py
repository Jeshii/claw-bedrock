"""Tests for per-model cost awareness.

Costs are stored per 1M tokens and published to LiteLLM per token, which is
what its cost-based router actually reads.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=test-key python3 -m pytest tests/ -v
"""

import base64
import math
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


def encode(name: str) -> str:
    """Mirror management_app.base64url_encode for PATCH paths."""
    return base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")


def add_model(client, name, **extra):
    body = {
        "model_name": name,
        "provider": "bedrock",
        "litellm_params": {"model": f"bedrock_mantle/{name}", "context_length": 200000},
    }
    body.update(extra)
    return client.post("/api/models", json=body)


def patch_model(client, name, body):
    return client.patch(f"/api/models/{encode(name)}", json=body)


def patch_model_literal(client, name, field, literal):
    """PATCH with a hand-written JSON body, bypassing httpx's JSON encoder.

    httpx >= 0.28 encodes json= with allow_nan=False, so float("nan") and
    float("inf") can no longer be sent that way. Writing the bare token into a
    raw body still delivers a non-finite float to the app, which is what this
    needs to exercise (see management_app's math.isfinite guard).
    """
    return client.patch(
        f"/api/models/{encode(name)}",
        content=f'{{"{field}": {literal}}}',
        headers={"content-type": "application/json"},
    )


def emitted(db, model_name):
    """The single model_list entry get_models_for_litellm writes for a model."""
    for entry in db.get_models_for_litellm()["model_list"]:
        if entry["model_name"] == model_name:
            return entry
    return None


# ── Tests ───────────────────────────────────────────────────────────────────


class TestConfigEmission:
    """Costs must land where LiteLLM's cost-based router looks for them."""

    def test_cost_is_converted_to_per_token(self, test_env):
        """Stored $/1M becomes litellm_params per-token, which is what the router reads."""
        client, _tmpdir, db = test_env

        add_model(client, "sonnet", input_cost=3.0, output_cost=15.0)
        entry = emitted(db, "sonnet")

        assert entry["litellm_params"]["input_cost_per_token"] == 3.0e-06
        assert entry["litellm_params"]["output_cost_per_token"] == 1.5e-05

    def test_model_info_mirrors_cost(self, test_env):
        """model_info carries the per-token value for spend reporting."""
        client, _tmpdir, db = test_env

        add_model(client, "sonnet", input_cost=3.0, output_cost=15.0)
        info = emitted(db, "sonnet")["model_info"]

        assert info == {"input_cost": 3.0e-06, "output_cost": 1.5e-05}

    def test_model_info_is_merged_not_clobbered(self, test_env):
        """A model that already has model_info keeps its other keys."""
        client, _tmpdir, db = test_env

        add_model(
            client,
            "qwen",
            model_info={"supports_tool_calling": True},
            input_cost=0.0,
        )
        info = emitted(db, "qwen")["model_info"]

        assert info["supports_tool_calling"] is True
        assert info["input_cost"] == 0.0

    def test_free_model_keeps_a_zero_cost(self, test_env):
        """0.0 is a real price, not a missing value -- `if not cost` would drop it."""
        client, _tmpdir, db = test_env

        add_model(client, "free", input_cost=0, output_cost=0)
        entry = emitted(db, "free")

        assert entry["litellm_params"]["input_cost_per_token"] == 0.0
        assert entry["litellm_params"]["output_cost_per_token"] == 0.0

    def test_no_cost_emits_nothing(self, test_env):
        """A model with no cost gets no cost keys at all."""
        client, _tmpdir, db = test_env

        add_model(client, "sonnet")
        entry = emitted(db, "sonnet")

        assert "input_cost_per_token" not in entry["litellm_params"]
        assert "output_cost_per_token" not in entry["litellm_params"]
        assert "model_info" not in entry

    def test_output_only_cost(self, test_env):
        """Either price can be recorded without the other."""
        client, _tmpdir, db = test_env

        add_model(client, "in-only", input_cost=1.25)
        entry = emitted(db, "in-only")

        assert entry["litellm_params"]["input_cost_per_token"] == 1.25e-06
        assert "output_cost_per_token" not in entry["litellm_params"]

    def test_grouped_model_keeps_costs(self, test_env):
        """A grouped model is renamed to the group name, but keeps its pricing."""
        client, _tmpdir, db = test_env

        add_model(client, "sonnet-v2", model_group="sonnet", input_cost=3.0)
        entry = emitted(db, "claw-bedrock/sonnet")

        assert entry["litellm_params"]["input_cost_per_token"] == 3.0e-06


class TestConfigCleanliness:
    """UI bookkeeping must not leak into the generated config."""

    def test_ui_only_fields_are_stripped(self, test_env):
        """The config holds what LiteLLM consumes, not the UI's own columns."""
        client, _tmpdir, db = test_env

        add_model(
            client,
            "sonnet",
            input_cost=3.0,
            output_cost=15.0,
            tags=["prod"],
            reasoning_effort="high",
        )
        entry = emitted(db, "sonnet")

        for field in (
            "provider",
            "tags",
            "reasoning_effort",
            "input_cost",
            "output_cost",
        ):
            assert field not in entry, f"{field} leaked into the config"

    def test_unknown_litellm_keys_survive(self, test_env):
        """A denylist, not an allowlist: rpm/tpm are real LiteLLM settings.

        Records migrated from an older config.local.yaml keep whatever the
        model_list had, so top-level LiteLLM keys must pass through.
        """
        client, _tmpdir, db = test_env

        add_model(client, "sonnet", rpm=1000, tpm=500000)
        entry = emitted(db, "sonnet")

        assert entry["rpm"] == 1000
        assert entry["tpm"] == 500000

    def test_stripping_does_not_mutate_the_stored_record(self, test_env):
        """Config generation reads the record, it does not rewrite it."""
        client, _tmpdir, db = test_env

        add_model(client, "sonnet", input_cost=3.0, tags=["prod"])
        db.get_models_for_litellm()

        stored = db.get_model_by_name("sonnet")
        assert stored["input_cost"] == 3.0
        assert stored["tags"] == ["prod"]
        assert "input_cost_per_token" not in stored["litellm_params"]


class TestPatchModelCost:
    """PATCH /api/models/{name} cost handling."""

    def test_sets_both_costs(self, test_env):
        client, _tmpdir, db = test_env

        add_model(client, "sonnet")
        resp = patch_model(client, "sonnet", {"input_cost": 3.0, "output_cost": 15.0})

        assert resp.status_code == 200
        stored = db.get_model_by_name("sonnet")
        assert stored["input_cost"] == 3.0
        assert stored["output_cost"] == 15.0
        assert (
            emitted(db, "sonnet")["litellm_params"]["input_cost_per_token"] == 3.0e-06
        )

    def test_numeric_string_is_accepted(self, test_env):
        """A text input sends a string; it is parsed, not rejected."""
        client, _tmpdir, db = test_env

        add_model(client, "sonnet")
        resp = patch_model(client, "sonnet", {"input_cost": "2.5"})

        assert resp.status_code == 200
        assert db.get_model_by_name("sonnet")["input_cost"] == 2.5

    def test_updates_one_side_without_touching_the_other(self, test_env):
        client, _tmpdir, db = test_env

        add_model(client, "sonnet", input_cost=3.0, output_cost=15.0)
        patch_model(client, "sonnet", {"input_cost": 1.0})

        stored = db.get_model_by_name("sonnet")
        assert stored["input_cost"] == 1.0
        assert stored["output_cost"] == 15.0

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_blank_clears_the_cost(self, test_env, blank):
        """Clearing removes the key so the model stops claiming a price."""
        client, _tmpdir, db = test_env

        add_model(client, "sonnet", input_cost=3.0, output_cost=15.0)
        resp = patch_model(
            client, "sonnet", {"input_cost": blank, "output_cost": blank}
        )

        assert resp.status_code == 200
        assert resp.json()["cleared"] == ["input_cost", "output_cost"]
        stored = db.get_model_by_name("sonnet")
        assert "input_cost" not in stored
        assert "output_cost" not in stored
        assert "input_cost_per_token" not in emitted(db, "sonnet")["litellm_params"]

    @pytest.mark.parametrize(
        "bad",
        [
            -1.0,
            "not a number",
            float("nan"),
            float("inf"),
            "NaN",
            "Infinity",
            "-0.5",
            True,
            [3.0],
            {"v": 3.0},
        ],
    )
    def test_rejects_unusable_costs(self, test_env, bad):
        """A bad price is a 400 and the stored cost is left alone."""
        client, _tmpdir, db = test_env

        add_model(client, "sonnet", input_cost=3.0)
        if isinstance(bad, float) and not math.isfinite(bad):
            literal = (
                "NaN" if math.isnan(bad) else ("Infinity" if bad > 0 else "-Infinity")
            )
            resp = patch_model_literal(client, "sonnet", "input_cost", literal)
        else:
            resp = patch_model(client, "sonnet", {"input_cost": bad})

        assert resp.status_code == 400, resp.text
        assert db.get_model_by_name("sonnet")["input_cost"] == 3.0

    def test_rejection_does_not_partially_apply(self, test_env):
        """A valid cost alongside an invalid one applies neither."""
        client, _tmpdir, db = test_env

        add_model(client, "sonnet", input_cost=3.0)
        resp = patch_model(client, "sonnet", {"input_cost": 1.0, "output_cost": "nope"})

        assert resp.status_code == 400
        stored = db.get_model_by_name("sonnet")
        assert stored["input_cost"] == 3.0
        assert "output_cost" not in stored

    def test_zero_is_not_treated_as_blank(self, test_env):
        client, _tmpdir, db = test_env

        add_model(client, "sonnet", input_cost=3.0)
        resp = patch_model(client, "sonnet", {"input_cost": 0})

        assert resp.status_code == 200
        assert resp.json()["cleared"] == []
        assert db.get_model_by_name("sonnet")["input_cost"] == 0.0

    def test_unknown_model_is_404(self, test_env):
        client, _tmpdir, _db = test_env

        resp = patch_model(client, "ghost", {"input_cost": 1.0})

        assert resp.status_code == 404


class TestGroupsPayload:
    """The Groups dashboard renders costs, so the endpoint must carry them."""

    def test_members_expose_costs(self, test_env):
        client, _tmpdir, _db = test_env

        add_model(
            client, "sonnet", model_group="sonnet", input_cost=3.0, output_cost=15.0
        )

        data = client.get("/api/model-groups").json()
        member = data["groups"][0]["members"][0]

        assert member["input_cost"] == 3.0
        assert member["output_cost"] == 15.0

    def test_payload_reports_active_strategy(self, test_env):
        """The UI warns about costless members only under cost-based routing."""
        client, _tmpdir, _db = test_env

        add_model(client, "sonnet", model_group="sonnet")
        assert client.get("/api/model-groups").json()["routing_strategy"] is None

        client.post("/api/settings/router", json={"routing_strategy": "cost-based"})
        data = client.get("/api/model-groups").json()

        # The short form is normalized, so the UI compares against the literal.
        assert data["routing_strategy"] == "cost-based-routing"
