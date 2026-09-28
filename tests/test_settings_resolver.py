"""Unit tests for settings_resolver precedence.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=<fernet-key> python3 -m pytest tests/ -q
"""

import os
import sys
import tempfile
from dataclasses import FrozenInstanceError

import pytest


@pytest.fixture
def resolver_env(monkeypatch):
    """Isolated CONFIG_DIR plus a freshly imported resolver, per test.

    The resolver reads the config layer through `db`, which opens TinyDB at
    import time, so both modules have to be re-imported for each test.
    """
    for name in ("AWS_REGION", "AWS_PROFILE"):
        monkeypatch.delenv(name, raising=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("CONFIG_DIR", tmpdir)
        monkeypatch.setenv(
            "ENCRYPTION_KEY", "7et38Ta-TUAjQExZycUZs4X1HZq9CqfgCoVcQmJjvMs="
        )
        for mod in [
            m for m in sys.modules if m.startswith(("db", "settings_resolver"))
        ]:
            del sys.modules[mod]
        import db as db_mod
        import settings_resolver as sr

        yield sr, db_mod, monkeypatch

        for mod in [
            m for m in sys.modules if m.startswith(("db", "settings_resolver"))
        ]:
            del sys.modules[mod]


class TestPrecedence:
    def test_default_used_when_nothing_set(self, resolver_env):
        sr, _db, _mp = resolver_env
        r = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        )
        assert r.value == "ap-northeast-1"
        assert r.source == "default"
        assert r.shadowed == ()
        assert not r.is_shadowed

    def test_config_beats_default(self, resolver_env):
        sr, db_mod, _mp = resolver_env
        db_mod.set_setting("bedrock_region", "eu-west-1")
        r = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        )
        assert r.value == "eu-west-1"
        assert r.source == "config"
        # `default` is a fallback, not something the user chose, so it is not
        # reported as shadowed.
        assert r.shadowed == ()
        assert not r.is_shadowed

    def test_env_beats_config(self, resolver_env):
        sr, db_mod, mp = resolver_env
        db_mod.set_setting("bedrock_region", "eu-west-1")
        mp.setenv("AWS_REGION", "us-west-2")
        r = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        )
        assert r.value == "us-west-2"
        assert r.source == "env"
        assert r.shadowed == ("config",)

    def test_flag_beats_env(self, resolver_env):
        sr, db_mod, mp = resolver_env
        db_mod.set_setting("bedrock_region", "eu-west-1")
        mp.setenv("AWS_REGION", "us-west-2")
        r = sr.resolve(
            "AWS_REGION",
            config_key="bedrock_region",
            flag="sa-east-1",
            default="ap-northeast-1",
        )
        assert r.value == "sa-east-1"
        assert r.source == "flag"
        assert r.shadowed == ("env", "config")

    def test_shadowed_lists_set_layers_only(self, resolver_env):
        sr, db_mod, mp = resolver_env
        db_mod.set_setting("bedrock_region", "eu-west-1")
        mp.setenv("AWS_REGION", "us-west-2")
        r = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        )
        assert r.shadowed == ("config",)
        assert r.is_shadowed
        assert "default" not in r.shadowed

    def test_default_still_reported_when_it_wins(self, resolver_env):
        sr, _db, _mp = resolver_env
        r = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        )
        assert r.source == "default"

    def test_winner_not_listed_in_own_shadowed(self, resolver_env):
        sr, db_mod, _mp = resolver_env
        db_mod.set_setting("bedrock_region", "eu-west-1")
        r = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        )
        assert "config" not in r.shadowed


class TestEmptyValues:
    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_env_does_not_outrank_config(self, resolver_env, blank):
        sr, db_mod, mp = resolver_env
        db_mod.set_setting("bedrock_region", "eu-west-1")
        mp.setenv("AWS_REGION", blank)
        r = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        )
        assert r.value == "eu-west-1"
        assert r.source == "config"

    def test_blank_config_does_not_outrank_default(self, resolver_env):
        sr, db_mod, _mp = resolver_env
        db_mod.set_setting("bedrock_region", "  ")
        r = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        )
        assert r.source == "default"
        assert r.value == "ap-northeast-1"

    def test_blank_flag_is_ignored(self, resolver_env):
        sr, _db, mp = resolver_env
        mp.setenv("AWS_REGION", "us-west-2")
        r = sr.resolve("AWS_REGION", flag="", default="ap-northeast-1")
        assert r.source == "env"
        assert r.value == "us-west-2"


class TestNamespacing:
    def test_env_var_name_is_not_used_as_a_config_key(self, resolver_env):
        """Config import writes keys verbatim, so env names must not be reused."""
        sr, db_mod, _mp = resolver_env
        db_mod.set_setting("AWS_REGION", "should-not-win")
        r = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        )
        assert r.value == "ap-northeast-1"
        assert r.source == "default"

    def test_config_key_omitted_means_no_config_layer(self, resolver_env):
        sr, db_mod, _mp = resolver_env
        db_mod.set_setting("bedrock_region", "eu-west-1")
        r = sr.resolve("AWS_REGION", default="ap-northeast-1")
        assert r.source == "default"

    def test_resolve_without_env_var_name(self, resolver_env):
        sr, db_mod, _mp = resolver_env
        db_mod.set_setting("bedrock_region", "eu-west-1")
        r = sr.resolve(None, config_key="bedrock_region", default="ap-northeast-1")
        assert r.value == "eu-west-1"
        assert r.source == "config"


class TestStatelessness:
    def test_reads_fresh_each_call(self, resolver_env):
        """No caching — a later call must observe a value set after the first."""
        sr, _db, mp = resolver_env
        first = sr.resolve("AWS_REGION", default="ap-northeast-1")
        assert first.source == "default"
        mp.setenv("AWS_REGION", "us-west-2")
        second = sr.resolve("AWS_REGION", default="ap-northeast-1")
        assert second.source == "env"
        assert second.value == "us-west-2"

    def test_no_state_captured_at_import(self, resolver_env):
        sr, _db, mp = resolver_env
        mp.setenv("AWS_PROFILE", "set-after-import")
        r = sr.resolve("AWS_PROFILE", default="bedrock-openai20b")
        assert r.value == "set-after-import"


class TestSerialization:
    def test_as_dict_shape(self, resolver_env):
        sr, _db, mp = resolver_env
        mp.setenv("AWS_REGION", "us-west-2")
        d = sr.resolve(
            "AWS_REGION", config_key="bedrock_region", default="ap-northeast-1"
        ).as_dict()
        assert d == {
            "value": "us-west-2",
            "source": "env",
            "shadowed": [],
        }

    def test_as_dict_is_json_safe(self, resolver_env):
        import json

        sr, _db, _mp = resolver_env
        json.dumps(sr.resolve("AWS_REGION", default="ap-northeast-1").as_dict())

    def test_resolved_is_frozen(self, resolver_env):
        sr, _db, _mp = resolver_env
        r = sr.resolve("AWS_REGION", default="ap-northeast-1")
        with pytest.raises(FrozenInstanceError):
            r.value = "mutated"


class TestPurity:
    def test_resolve_does_not_write_config(self, resolver_env):
        sr, db_mod, _mp = resolver_env
        before = db_mod.get_settings()
        sr.resolve("AWS_REGION", config_key="bedrock_region", default="ap-northeast-1")
        assert db_mod.get_settings() == before

    def test_resolve_does_not_mutate_environment(self, resolver_env):
        sr, _db, _mp = resolver_env
        os.environ.pop("AWS_REGION", None)
        sr.resolve("AWS_REGION", default="ap-northeast-1")
        assert "AWS_REGION" not in os.environ
