"""Tests for the conversations SQLite data layer."""

import json
import os
import sys
import tempfile
import time

import pytest


def _clean_import_modules():
    keys = [k for k in sys.modules if k.startswith(("conversations_db",))]
    for k in keys:
        del sys.modules[k]


@pytest.fixture(autouse=True)
def test_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CONFIG_DIR"] = tmpdir
        _clean_import_modules()
        import conversations_db as cdb

        cdb.init_db()
        yield cdb


def test_create_and_get(test_env):
    cdb = test_env
    cid = cdb.create_conversation("Title", "model-x")
    conv = cdb.get_conversation(cid)
    assert conv is not None
    assert conv["title"] == "Title"
    assert conv["model"] == "model-x"
    assert conv["id"] == cid


def test_get_missing_returns_none(test_env):
    cdb = test_env
    assert cdb.get_conversation("nope") is None


def test_list_empty(test_env):
    cdb = test_env
    assert cdb.list_conversations() == []


def test_list_ordering_newest_first(test_env):
    cdb = test_env
    cdb.create_conversation("A", "m")
    b = cdb.create_conversation("B", "m")
    ids = [c["id"] for c in cdb.list_conversations()]
    assert ids[0] == b


def test_update_conversation(test_env):
    cdb = test_env
    cid = cdb.create_conversation("T", "m")
    assert cdb.update_conversation(cid, title="New") is True
    assert cdb.get_conversation(cid)["title"] == "New"
    assert cdb.update_conversation(cid, model="m2") is True
    assert cdb.get_conversation(cid)["model"] == "m2"
    assert cdb.update_conversation("missing", title="x") is False


def test_delete_conversation(test_env):
    cdb = test_env
    cid = cdb.create_conversation("T", "m")
    assert cdb.delete_conversation(cid) is True
    assert cdb.get_conversation(cid) is None
    assert cdb.delete_conversation(cid) is False


def test_delete_cascades_messages(test_env):
    cdb = test_env
    cid = cdb.create_conversation("T", "m")
    cdb.add_message(cid, "user", "hi")
    cdb.delete_conversation(cid)
    assert cdb.get_messages(cid) == []


def test_add_and_get_messages(test_env):
    cdb = test_env
    cid = cdb.create_conversation("T", "m")
    mid = cdb.add_message(cid, "user", "hello")
    assert isinstance(mid, int)
    msgs = cdb.get_messages(cid)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hello"
    assert msgs[0]["tool_calls"] is None


def test_tool_calls_serialized(test_env):
    cdb = test_env
    cid = cdb.create_conversation("T", "m")
    cdb.add_message(cid, "assistant", "hi", tool_calls=[{"name": "x"}])
    msgs = cdb.get_messages(cid)
    assert msgs[0]["tool_calls"] == json.dumps([{"name": "x"}])


def test_touch_updates_timestamp(test_env):
    cdb = test_env
    cid = cdb.create_conversation("T", "m")
    before = cdb.get_conversation(cid)["updated_at"]
    time.sleep(0.01)
    cdb.touch_conversation(cid)
    after = cdb.get_conversation(cid)["updated_at"]
    assert after > before


def test_add_message_touches_conversation(test_env):
    cdb = test_env
    cid = cdb.create_conversation("T", "m")
    before = cdb.get_conversation(cid)["updated_at"]
    time.sleep(0.01)
    cdb.add_message(cid, "user", "hi")
    after = cdb.get_conversation(cid)["updated_at"]
    assert after > before


def test_search_by_title(test_env):
    cdb = test_env
    a = cdb.create_conversation("Alpha chat", "m")
    b = cdb.create_conversation("Beta chat", "m")
    res = [c["id"] for c in cdb.list_conversations(search="Alpha")]
    assert a in res and b not in res


def test_search_by_message_body(test_env):
    cdb = test_env
    a = cdb.create_conversation("T1", "m")
    b = cdb.create_conversation("T2", "m")
    cdb.add_message(a, "user", "needle in haystack")
    res = [c["id"] for c in cdb.list_conversations(search="needle")]
    assert a in res and b not in res


def test_search_case_insensitive(test_env):
    cdb = test_env
    cid = cdb.create_conversation("Hello World", "m")
    res = [c["id"] for c in cdb.list_conversations(search="hello")]
    assert cid in res


def test_pagination(test_env):
    cdb = test_env
    for i in range(5):
        cdb.create_conversation(f"T{i}", "m")
    page1 = cdb.list_conversations(page=1, per_page=2)
    page2 = cdb.list_conversations(page=2, per_page=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert page1[0]["id"] != page2[0]["id"]


def test_pagination_limit_offset(test_env):
    cdb = test_env
    for i in range(5):
        cdb.create_conversation(f"T{i}", "m")
    page = cdb.list_conversations(limit=2, offset=2)
    assert len(page) == 2


def test_count_conversations(test_env):
    cdb = test_env
    cdb.create_conversation("A", "m")
    cdb.create_conversation("B", "m")
    assert cdb.count_conversations() == 2
    assert cdb.count_conversations(search="A") == 1


def test_foreign_key_enforcement(test_env):
    import sqlite3

    cdb = test_env
    conn = cdb._connect() if hasattr(cdb, "_connect") else None
    # _connect is private; instead test via sqlite directly with FK
    # Verify foreign_keys pragma is on by attempting bad insert
    import os

    dbpath = os.path.join(os.environ["CONFIG_DIR"], "conversations.db")
    conn = __import__("sqlite3").connect(dbpath)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("nonexistent", "user", "x", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    finally:
        conn.close()
    assert raised
