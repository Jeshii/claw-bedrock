"""Tests for the litellm_settings block of the generated config.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=test-key python3 -m pytest tests/ -v
"""

import importlib
import inspect
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

        import db as db_mod

        yield db_mod


class TestCallbacks:
    """litellm_settings.callbacks must survive litellm's startup validation."""

    def test_callbacks_resolve_to_instance_not_class(self, test_env):
        """A class in callbacks is a hard startup error for litellm >= 1.102.

        litellm resolves each entry and rejects a class with ValueError, so the
        proxy exits during startup, the watchdog restart-loops it, and every
        chat completion 502s. Point at the instance token_refresher.py builds
        at import time instead.
        """
        db = test_env
        import token_refresher as tr

        targets = db.get_litellm_settings()["callbacks"]
        assert targets, "callbacks must not be empty or the refresher never runs"

        for target in targets:
            module_name, _, attr = target.partition(".")
            obj = getattr(importlib.import_module(module_name), attr)
            assert not inspect.isclass(obj), (
                f"{target} resolves to a class; litellm >= 1.102 rejects that"
            )
            assert isinstance(obj, tr.CustomLogger), f"{target} is not a CustomLogger"

    def test_callback_target_is_the_shared_instance(self, test_env):
        """The proxy should use the one instance the rest of the app uses.

        Otherwise the proxy process builds a second BedrockTokenRefresher and
        its login state diverges from the Management UI's view of it.
        """
        db = test_env
        import token_refresher as tr

        (target,) = db.get_litellm_settings()["callbacks"]
        module_name, _, attr = target.partition(".")
        obj = getattr(importlib.import_module(module_name), attr)
        assert obj is tr.token_refresher

    def test_master_key_omitted_when_unset(self, test_env):
        """No master key configured means the key is left out entirely."""
        assert "master_key" not in test_env.get_litellm_settings()

    def test_master_key_included_when_set(self, test_env):
        """A configured master key is carried into litellm_settings."""
        db = test_env
        db.set_setting("litellm_master_key", "sk-test-123")
        assert db.get_litellm_settings()["master_key"] == "sk-test-123"
