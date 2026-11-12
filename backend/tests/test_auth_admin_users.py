import sqlite3

import pytest
from fastapi import HTTPException

from app.routers import auth


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            password_hash TEXT,
            display_name TEXT,
            role TEXT,
            status TEXT,
            created_at TEXT DEFAULT '2026-09-02 12:00:00'
        );
        CREATE TABLE operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            actor_name TEXT DEFAULT '',
            actor_role TEXT DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id INTEGER,
            entity_name TEXT DEFAULT '',
            detail_json TEXT DEFAULT '',
            created_at TEXT DEFAULT '2026-09-02 12:00:00'
        );
        """
    )
    conn.execute("INSERT INTO users VALUES (1, '老板', 'x', '老板', 'business_admin', 'active', '2026-09-02 10:00:00')")
    conn.execute("INSERT INTO users VALUES (2, '高贺雅姝', 'x', '高贺雅姝', 'super_admin', 'active', '2026-09-02 10:00:00')")
    conn.execute("INSERT INTO users VALUES (3, '顾问A', 'x', '顾问A', 'consultant', 'active', '2026-09-02 10:00:00')")
    conn.commit()
    return conn


def test_list_admin_users_hides_protected_super_admin_from_other_accounts(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(auth, "get_conn", lambda: conn)

    payload = auth.list_admin_users({"id": 1, "role": "business_admin"})

    assert [item["username"] for item in payload["items"]] == ["老板", "顾问A"]


def test_protected_super_admin_can_see_own_account(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(auth, "get_conn", lambda: conn)

    payload = auth.list_admin_users({"id": 2, "username": "高贺雅姝", "display_name": "高贺雅姝", "role": "super_admin"})

    assert [item["username"] for item in payload["items"]] == ["老板", "高贺雅姝", "顾问A"]


def test_business_admin_updates_consultant_role_and_logs(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(auth, "get_conn", lambda: conn)
    monkeypatch.setattr(auth.feishu_store, "push_user", lambda *args, **kwargs: None)

    updated = auth.update_admin_user(
        3,
        auth.MemberUpdate(role="business_admin"),
        {"id": 1, "username": "老板", "display_name": "老板", "role": "business_admin"},
    )

    assert updated["role"] == "business_admin"
    log = conn.execute("SELECT action, entity_type, entity_id FROM operation_logs").fetchone()
    assert dict(log) == {"action": "update", "entity_type": "user", "entity_id": 3}


def test_business_admin_cannot_change_super_admin(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(auth, "get_conn", lambda: conn)

    with pytest.raises(HTTPException) as exc:
        auth.update_admin_user(
            2,
            auth.MemberUpdate(role="consultant"),
            {"id": 1, "username": "老板", "display_name": "老板", "role": "business_admin"},
        )

    assert exc.value.status_code == 404
