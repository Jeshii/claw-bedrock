"""Tests for the Playground V2 conversation REST API.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=<fernet-key> python3 -m pytest tests/test_conversations_api.py -q
"""

import sys
import types

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


def _clean_import_modules():
    prefixes = (
        "db",
        "management_app",
        "conversations_db",
        "encryption_utils",
        "settings_resolver",
        "token_refresher",
    )
    for k in [k for k in sys.modules if k.startswith(prefixes)]:
        del sys.modules[k]


class _FakeRefresher:
    def __init__(self):
        self._region = "ap-northeast-1"
        self._profile = "default"

    def auth_status_payload(self):
        return {
            "needs_login": False,
            "auth_url": None,
            "awaiting_code": False,
            "auth_error": None,
            "profile": self._profile,
            "region": self._region,
            "static_keys": {"configured": False, "state": "absent"},
            "token": {
                "present": False,
                "age_seconds": None,
                "ttl_seconds": 2700,
                "expires_in": None,
                "stale": True,
            },
        }

    def configure(self, *a, **k):
        return True

    def submit_code(self, code):
        return None

    def retry_login(self):
        return None


def _install_fake_token_refresher():
    mod = types.ModuleType("token_refresher")
    mod.token_refresher = _FakeRefresher()
    sys.modules["token_refresher"] = mod


class _FakeResp:
    status_code = 200

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=None):
        yield b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    def close(self):
        return None


@pytest.fixture
def test_env(monkeypatch, tmp_path):
    for name in (
        "AWS_REGION",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    _clean_import_modules()
    _install_fake_token_refresher()

    import management_app as mgmt

    monkeypatch.setattr(mgmt, "_reload_litellm_config", lambda: True, raising=False)
    monkeypatch.setattr(mgmt, "merge_configs", lambda: None, raising=False)
    monkeypatch.setattr(mgmt.requests, "post", lambda *a, **k: _FakeResp())

    mgmt.conversations_db.init_db()
    client = TestClient(mgmt.app)
    yield client, mgmt
    _clean_import_modules()


def test_list_empty(test_env):
    client, _ = test_env
    r = client.get("/api/conversations")
    assert r.status_code == 200
    data = r.json()
    assert data["conversations"] == []
    assert data["total"] == 0


def test_create_empty_returns_json(test_env):
    client, _ = test_env
    r = client.post("/api/conversations", json={"title": "T", "model": "m"})
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "T"
    assert data["model"] == "m"
    assert "id" in data


def test_create_requires_model(test_env):
    client, _ = test_env
    r = client.post("/api/conversations", json={"title": "T"})
    assert r.status_code == 400


def test_create_with_first_message_streams(test_env):
    client, _ = test_env
    r = client.post(
        "/api/conversations",
        json={"title": "T", "model": "m", "first_message": "hi"},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    assert "X-Conversation-ID" in r.headers
    cid = r.headers["X-Conversation-ID"]
    gr = client.get(f"/api/conversations/{cid}")
    assert gr.status_code == 200
    msgs = gr.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi"
    assert msgs[1]["content"] == "Hello"


def test_get_missing_404(test_env):
    client, _ = test_env
    r = client.get("/api/conversations/nope")
    assert r.status_code == 404


def test_update_conversation(test_env):
    client, _ = test_env
    r = client.post("/api/conversations", json={"title": "T", "model": "m"})
    cid = r.json()["id"]
    r2 = client.put(
        f"/api/conversations/{cid}", json={"title": "Renamed", "model": "m2"}
    )
    assert r2.status_code == 200
    assert r2.json()["title"] == "Renamed"
    assert r2.json()["model"] == "m2"


def test_update_missing_404(test_env):
    client, _ = test_env
    r = client.put("/api/conversations/nope", json={"title": "x"})
    assert r.status_code == 404


def test_delete_conversation(test_env):
    client, _ = test_env
    r = client.post("/api/conversations", json={"title": "T", "model": "m"})
    cid = r.json()["id"]
    r2 = client.delete(f"/api/conversations/{cid}")
    assert r2.status_code == 200
    assert client.get(f"/api/conversations/{cid}").status_code == 404


def test_delete_missing_404(test_env):
    client, _ = test_env
    r = client.delete("/api/conversations/nope")
    assert r.status_code == 404


def test_post_message_streams_and_persists(test_env):
    client, _ = test_env
    r = client.post("/api/conversations", json={"title": "T", "model": "m"})
    cid = r.json()["id"]
    r2 = client.post(
        f"/api/conversations/{cid}/messages",
        json={"content": "second"},
    )
    assert r2.status_code == 200
    assert "text/event-stream" in r2.headers.get("content-type", "")
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "second"
    assert msgs[1]["content"] == "Hello"


def test_post_message_missing_conv_404(test_env):
    client, _ = test_env
    r = client.post("/api/conversations/nope/messages", json={"content": "x"})
    assert r.status_code == 404


def test_list_search_and_pagination(test_env):
    client, _ = test_env
    for i in range(5):
        client.post("/api/conversations", json={"title": f"T{i}", "model": "m"})
    r = client.get("/api/conversations", params={"search": "T0"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    r2 = client.get("/api/conversations", params={"page": 1, "per_page": 2})
    assert len(r2.json()["conversations"]) == 2
    assert r2.json()["total"] == 5


def test_post_message_with_messages_array(test_env):
    client, _ = test_env
    r = client.post("/api/conversations", json={"title": "T", "model": "m"})
    cid = r.json()["id"]
    r2 = client.post(
        f"/api/conversations/{cid}/messages",
        json={"messages": [{"role": "user", "content": "arr"}]},
    )
    assert r2.status_code == 200
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert msgs[-2]["content"] == "arr"
    assert msgs[-1]["content"] == "Hello"
