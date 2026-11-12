import json
import sqlite3

import pytest

from app.routers import matching
from app.config import settings


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT
        );
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            title TEXT,
            company_id INTEGER
        );
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE match_runs (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER,
            status TEXT,
            error TEXT
        );
        CREATE TABLE match_results (
            id INTEGER PRIMARY KEY,
            run_id INTEGER,
            position_id INTEGER,
            final_score INTEGER,
            recommend_level TEXT,
            dims_json TEXT,
            detail_rows_json TEXT,
            suggestions_json TEXT
        );
        CREATE TABLE chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            run_ids_json TEXT NOT NULL DEFAULT '[]',
            added_result_ids_json TEXT NOT NULL DEFAULT '[]',
            hidden_result_ids_json TEXT NOT NULL DEFAULT '[]',
            title TEXT DEFAULT '',
            created_at TEXT DEFAULT '2026-08-25 12:00:00',
            updated_at TEXT DEFAULT '2026-08-25 12:00:00',
            UNIQUE(user_id, run_ids_json)
        );
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            role TEXT,
            content TEXT,
            model TEXT DEFAULT '',
            tokens INTEGER DEFAULT 0,
            feishu_record_id TEXT DEFAULT '',
            created_at TEXT DEFAULT '2026-08-25 12:00:00'
        );
        """
    )
    conn.execute("INSERT INTO users VALUES (1, 'u', '用户')")
    conn.execute("INSERT INTO candidates VALUES (1, '张三')")
    conn.execute("INSERT INTO companies VALUES (1, '客户A')")
    conn.execute("INSERT INTO positions VALUES (10, 'Java后端', 1)")
    conn.execute("INSERT INTO positions VALUES (11, '广告研发', 1)")
    conn.execute("INSERT INTO match_runs VALUES (100, 1, 'done', '')")
    conn.execute(
        "INSERT INTO match_results VALUES (1000, 100, 10, 90, 'top', ?, ?, ?)",
        (
            json.dumps({"judgement": "建议推荐"}, ensure_ascii=False),
            json.dumps([{"requirement": "岗位主线一致性", "condition_type": "core", "label": "满足", "note": "一致"}], ensure_ascii=False),
            "[]",
        ),
    )
    conn.execute("INSERT INTO match_results VALUES (1001, 100, 11, 70, 'caution', '{}', '[]', '[]')")
    conn.commit()
    return conn


def test_result_scoped_chat_session_uses_distinct_scope_and_title():
    conn = _conn()
    user = {"id": 1}

    run_session_id = matching._get_or_create_chat_session(conn, [100], user)
    result_session_id = matching._get_or_create_chat_session(conn, [100], user, result_id=1000)

    assert result_session_id != run_session_id
    payload = matching._chat_session_payload(conn, result_session_id, user)
    assert payload["run_ids"] == [100]
    assert payload["result_id"] == 1000
    assert payload["title"] == "张三×Java后端"


def test_result_scoped_context_only_loads_one_report():
    conn = _conn()

    context = matching._load_done_match_context(conn, [100], result_id=1000)

    assert len(context) == 1
    assert context[0]["报告ID"] == 1000
    assert context[0]["项目"] == "Java后端"


def test_selected_result_ids_scope_chat_context():
    conn = _conn()

    context = matching._load_done_match_context(conn, [100], selected_result_ids=[1001])

    assert len(context) == 1
    assert context[0]["报告ID"] == 1001
    assert context[0]["项目"] == "广告研发"


def test_get_current_chat_session_is_read_only(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(matching, "get_conn", lambda: conn)

    with pytest.raises(matching.HTTPException) as exc:
        matching.get_current_chat_session(run_ids="100", user={"id": 1})

    assert exc.value.status_code == 404
    assert conn.execute("SELECT COUNT(*) AS c FROM chat_sessions").fetchone()["c"] == 0

    payload = matching.create_current_chat_session(matching.MatchSummaryIn(run_ids=[100]), {"id": 1})

    assert payload["session_id"]
    assert conn.execute("SELECT COUNT(*) AS c FROM chat_sessions").fetchone()["c"] == 1


def test_workspace_state_is_user_and_scope_scoped(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(matching, "get_conn", lambda: conn)

    empty = matching.get_chat_workspace_state(run_ids="100", user={"id": 1})
    assert empty == {"added_result_ids": [], "hidden_result_ids": []}

    saved = matching.save_chat_workspace_state(
        matching.MatchWorkspaceStateIn(run_ids=[100], added_result_ids=[1001, 1001], hidden_result_ids=[1000, 1001, 1000]),
        {"id": 1},
    )

    assert saved == {"added_result_ids": [1001], "hidden_result_ids": [1000, 1001]}
    assert matching.get_chat_workspace_state(run_ids="100", user={"id": 1}) == {
        "added_result_ids": [1001],
        "hidden_result_ids": [1000, 1001],
    }
    assert matching.get_chat_workspace_state(run_ids="100", user={"id": 2}) == {"added_result_ids": [], "hidden_result_ids": []}


def test_workspace_state_repairs_old_chat_schema(monkeypatch):
    conn = _conn()
    conn.executescript(
        """
        DROP TABLE chat_messages;
        DROP TABLE chat_sessions;
        CREATE TABLE chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            run_ids_json TEXT NOT NULL DEFAULT '[]',
            title TEXT DEFAULT ''
        );
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            role TEXT,
            content TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO chat_sessions (user_id, run_ids_json, title) VALUES (?,?,?)",
        (1, matching._chat_scope_json([100]), "旧会话"),
    )
    conn.commit()
    monkeypatch.setattr(matching, "get_conn", lambda: conn)

    state = matching.get_chat_workspace_state(run_ids="100", user={"id": 1})

    assert state == {"added_result_ids": [], "hidden_result_ids": []}
    session_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
    message_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    assert {"added_result_ids_json", "hidden_result_ids_json", "updated_at"}.issubset(session_cols)
    assert {"model", "tokens", "feishu_record_id"}.issubset(message_cols)


def test_delete_chat_session_removes_only_current_user_session(monkeypatch):
    conn = _conn()
    user = {"id": 1}
    own_session_id = matching._get_or_create_chat_session(conn, [100], user)
    other_session_id = matching._get_or_create_chat_session(conn, [100], {"id": 2})
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?,?,?)",
        (own_session_id, "user", "请解释这份报告"),
    )
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?,?,?)",
        (other_session_id, "user", "其他用户消息"),
    )
    conn.commit()
    monkeypatch.setattr(matching, "get_conn", lambda: conn)

    assert matching.delete_chat_session(own_session_id, user) == {"ok": True}

    assert conn.execute("SELECT COUNT(*) AS c FROM chat_sessions WHERE id=?", (own_session_id,)).fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM chat_messages WHERE session_id=?", (own_session_id,)).fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM chat_sessions WHERE id=?", (other_session_id,)).fetchone()["c"] == 1


def test_delete_history_result_removes_report_and_result_scoped_chat(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            title TEXT
        );
        CREATE TABLE match_runs (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER
        );
        CREATE TABLE match_results (
            id INTEGER PRIMARY KEY,
            run_id INTEGER,
            position_id INTEGER,
            feishu_record_id TEXT
        );
        CREATE TABLE client_recommendation_reports (
            id INTEGER PRIMARY KEY,
            result_id INTEGER,
            feishu_record_id TEXT
        );
        CREATE TABLE chat_sessions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            run_ids_json TEXT
        );
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY,
            session_id INTEGER,
            role TEXT,
            content TEXT,
            feishu_record_id TEXT
        );
        """
    )
    conn.execute("INSERT INTO candidates VALUES (1, '张三')")
    conn.execute("INSERT INTO positions VALUES (10, 'Java后端')")
    conn.execute("INSERT INTO match_runs VALUES (100, 1)")
    conn.execute("INSERT INTO match_results VALUES (1000, 100, 10, 'recMR')")
    conn.execute("INSERT INTO client_recommendation_reports VALUES (2000, 1000, 'recCR')")
    conn.execute("INSERT INTO chat_sessions VALUES (3000, 1, ?)", (matching._chat_scope_json([100], 1000),))
    conn.execute("INSERT INTO chat_sessions VALUES (3001, 1, ?)", (matching._chat_scope_json([100]),))
    conn.execute("INSERT INTO chat_messages VALUES (4000, 3000, 'user', '解释报告', 'recChat')")
    conn.execute("INSERT INTO chat_messages VALUES (4001, 3001, 'user', '比较候选人', '')")
    conn.commit()

    deleted = []

    class FakeClient:
        def delete_record(self, table_id, record_id):
            deleted.append((table_id, record_id))
            return {}

    monkeypatch.setattr(matching, "get_conn", lambda: conn)
    monkeypatch.setattr(matching.feishu_store.feishu, "client", FakeClient())
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_app_id", "app")
    monkeypatch.setattr(settings, "feishu_app_secret", "secret")
    monkeypatch.setattr(settings, "feishu_base_token", "base")
    monkeypatch.setattr(settings, "feishu_table_match_results", "tblMatch")
    monkeypatch.setattr(settings, "feishu_table_client_recommendations", "tblClient")
    monkeypatch.setattr(settings, "feishu_table_chat_messages", "tblChat")

    assert matching.delete_history_result(1000, {"id": 1})["ok"] is True

    assert ("tblMatch", "recMR") in deleted
    assert ("tblClient", "recCR") in deleted
    assert ("tblChat", "recChat") in deleted
    assert conn.execute("SELECT COUNT(*) AS c FROM match_results WHERE id=1000").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM client_recommendation_reports WHERE result_id=1000").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM chat_sessions WHERE id=3000").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM chat_sessions WHERE id=3001").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM chat_messages WHERE session_id IN (3000,3001)").fetchone()["c"] == 0


def test_delete_history_result_ignores_missing_feishu_bound_records(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            title TEXT
        );
        CREATE TABLE match_runs (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER
        );
        CREATE TABLE match_results (
            id INTEGER PRIMARY KEY,
            run_id INTEGER,
            position_id INTEGER,
            feishu_record_id TEXT
        );
        CREATE TABLE client_recommendation_reports (
            id INTEGER PRIMARY KEY,
            result_id INTEGER,
            feishu_record_id TEXT
        );
        CREATE TABLE chat_sessions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            run_ids_json TEXT
        );
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY,
            session_id INTEGER,
            role TEXT,
            content TEXT,
            feishu_record_id TEXT
        );
        """
    )
    conn.execute("INSERT INTO candidates VALUES (1, '张三')")
    conn.execute("INSERT INTO positions VALUES (10, 'Java后端')")
    conn.execute("INSERT INTO match_runs VALUES (100, 1)")
    conn.execute("INSERT INTO match_results VALUES (1000, 100, 10, 'recMissing')")
    conn.commit()

    class FakeClient:
        def delete_record(self, table_id, record_id):
            raise RuntimeError("飞书接口失败：HTTP 200, code=1254043, msg=RecordIdNotFound")

    monkeypatch.setattr(matching, "get_conn", lambda: conn)
    monkeypatch.setattr(matching.feishu_store.feishu, "client", FakeClient())
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_app_id", "app")
    monkeypatch.setattr(settings, "feishu_app_secret", "secret")
    monkeypatch.setattr(settings, "feishu_base_token", "base")
    monkeypatch.setattr(settings, "feishu_table_match_results", "tblMatch")

    assert matching.delete_history_result(1000, {"id": 1})["ok"] is True
    assert conn.execute("SELECT COUNT(*) AS c FROM match_results WHERE id=1000").fetchone()["c"] == 0
