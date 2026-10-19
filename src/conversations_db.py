"""SQLite persistence for Playground conversations."""

import contextlib
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime


def _config_dir() -> str:
    return os.environ.get("CONFIG_DIR", "/app")


def _db_path() -> str:
    return os.path.join(_config_dir(), "conversations.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv
    ON messages(conversation_id, created_at);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def close_db() -> None:
    with contextlib.suppress(Exception):
        conn = _connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()


def create_conversation(title: str, model: str) -> str:
    cid = str(uuid.uuid4())
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO conversations (id, title, model, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, title, model, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def get_conversation(conversation_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, title, model, created_at, updated_at "
            "FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()


def _search_clause(search: str) -> tuple[str, list]:
    if not search:
        return "", []
    like = f"%{search}%"
    clause = (
        "WHERE (LOWER(c.title) LIKE LOWER(?) "
        "OR EXISTS (SELECT 1 FROM messages m "
        "WHERE m.conversation_id = c.id AND LOWER(m.content) LIKE LOWER(?)))"
    )
    return clause, [like, like]


def list_conversations(
    search: str = "",
    page: int = 1,
    per_page: int = 50,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict]:
    if limit is not None:
        per_page = limit
    if per_page is None or per_page < 1:
        per_page = 50
    if offset is not None:
        page = (offset // per_page) + 1 if per_page else 1
    if page is None or page < 1:
        page = 1

    where_clause, params = _search_clause(search)
    sql = (
        "SELECT id, title, model, created_at, updated_at FROM conversations c "
        f"{where_clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    )
    params = params + [per_page, (page - 1) * per_page]
    conn = _connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_conversations(search: str = "") -> int:
    where_clause, params = _search_clause(search)
    sql = f"SELECT COUNT(*) FROM conversations c {where_clause}"
    conn = _connect()
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def update_conversation(
    conversation_id: str, title: str | None = None, model: str | None = None
) -> bool:
    fields = []
    values = []
    if title is not None:
        fields.append("title = ?")
        values.append(title)
    if model is not None:
        fields.append("model = ?")
        values.append(model)
    if not fields:
        return False
    values.append(conversation_id)
    conn = _connect()
    try:
        cur = conn.execute(
            f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_conversation(conversation_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    tool_calls=None,
) -> int:
    if tool_calls is not None and not isinstance(tool_calls, str):
        tool_calls = json.dumps(tool_calls)
    now = _now()
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, tool_calls, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, content, tool_calls, now),
        )
        conn.commit()
        row_id = int(cur.lastrowid)
    finally:
        conn.close()
    touch_conversation(conversation_id)
    return row_id


def get_messages(conversation_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, conversation_id, role, content, tool_calls, created_at "
            "FROM messages WHERE conversation_id = ? ORDER BY created_at, id",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def touch_conversation(conversation_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
        conn.commit()
    finally:
        conn.close()
