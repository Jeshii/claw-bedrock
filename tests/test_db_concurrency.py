"""Tests that TinyDB access is safe under concurrent requests.

Management UI handlers run in FastAPI's threadpool, so several can touch the
database at once. TinyDB's default JSONStorage re-opens and re-parses the whole
file on every access and takes no lock, so an unsynchronized read overlapping a
write sees a truncated file and raises JSONDecodeError. These tests hold that
line.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=test-key python3 -m pytest tests/ -v
"""

import json
import os
import sys
import tempfile
import threading
import time

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


def _hammer(db, readers=12, seconds=2.0):
    """Read and write the DB from many threads at once, collecting failures.

    Returns a dict of error type -> count. An empty dict means the storage
    layer survived the contention.
    """

    # A wide payload keeps each write on disk long enough that an unsynchronized
    # reader reliably lands inside it, so an unlocked regression shows up here
    # rather than flaking.
    def payload(i):
        return {
            "models": [
                {"model_name": f"m{j}", "input_cost": float(i)} for j in range(40)
            ]
        }

    db.set_setting("seed", payload(1))

    errors = {}
    lock = threading.Lock()
    stop = threading.Event()

    def record(exc):
        with lock:
            key = f"{type(exc).__name__}: {exc}"
            errors[key] = errors.get(key, 0) + 1

    def reader():
        while not stop.is_set():
            try:
                db.get_all_models()
                db.get_setting("seed")
                db.get_router_settings()
            except Exception as e:  # noqa: BLE001 - the point is to record anything
                record(e)

    def writer():
        i = 0
        while not stop.is_set():
            i += 1
            try:
                db.set_setting("seed", payload(i))
            except Exception as e:  # noqa: BLE001 - the point is to record anything
                record(e)

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(readers)]
    threads.append(threading.Thread(target=writer, daemon=True))
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(timeout=5)
    return errors


class TestConcurrentAccess:
    def test_reads_during_writes_never_hit_a_partial_file(self, test_env):
        """No reader may observe the database mid-write.

        Without the lock this reproduces the production failure -- JSONDecodeError
        "Expecting value: line 1 column 1 (char 0)" tens of thousands of times in
        a couple of seconds.
        """
        errors = _hammer(test_env)
        assert not errors, "concurrent access produced errors: " + "; ".join(
            f"{v}x {k}" for k, v in errors.items()
        )

    def test_database_stays_readable_after_contention(self, test_env):
        """The file on disk is still valid JSON once the threads stop."""
        _hammer(test_env, readers=6, seconds=1.0)
        with open(test_env.DB_PATH) as f:
            assert json.load(f) is not None

    def test_lock_is_reentrant(self, test_env):
        """import_backup and friends call other locked functions, so a plain
        Lock would deadlock them. Exercise that path under the test fixture."""
        payload = {
            "schema_version": 0,
            "data": {
                "models": [
                    {
                        "model_name": "m1",
                        "model_group": "g1",
                        "litellm_params": {"model": "bedrock_mantle/m1"},
                    }
                ],
                "tags": [{"name": "t1", "color": "#fff"}],
                "settings": {"k": "v"},
                "providers": [],
            },
        }
        summary = test_env.import_backup(payload)
        assert summary["models"] == 1
        assert summary["tags"] == 1
        assert test_env.get_setting("k") == "v"
        assert test_env.get_all_tags()[0]["name"] == "t1"

    def test_every_tinydb_accessor_is_serialized(self, test_env):
        """Guard against a new accessor being added without the decorator.

        Walks db.py and fails if any public function touches a table handle
        without @_serialized on it.
        """
        import ast
        import inspect

        src = inspect.getsource(sys.modules["db"])
        tree = ast.parse(src)
        table_names = {
            "models_table",
            "settings_table",
            "tags_table",
            "providers_table",
        }

        offenders = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            body = ast.unparse(node)
            decorators = {ast.unparse(d) for d in node.decorator_list}
            touches = any(f"{t}." in body for t in table_names) or "db.table(" in body
            delegates = any(
                f"{n}(" in body
                for n in (
                    "get_setting",
                    "set_setting",
                    "get_all_models",
                    "get_provider",
                )
            )
            if touches and "_serialized" not in decorators and not delegates:
                offenders.append(node.name)

        assert not offenders, (
            f"accessors touch TinyDB without @_serialized: {offenders}"
        )
