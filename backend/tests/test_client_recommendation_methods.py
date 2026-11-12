import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.routers import matching


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            resume_text TEXT,
            resume_struct_json TEXT
        );
        CREATE TABLE match_runs (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER
        );
        CREATE TABLE match_results (
            id INTEGER PRIMARY KEY,
            run_id INTEGER
        );
        CREATE TABLE client_recommendation_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            content_json TEXT NOT NULL,
            markdown TEXT NOT NULL DEFAULT '',
            model TEXT DEFAULT '',
            tokens INTEGER DEFAULT 0,
            status TEXT DEFAULT '草稿',
            feishu_record_id TEXT DEFAULT ''
        );
        CREATE TABLE ai_usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            endpoint TEXT NOT NULL DEFAULT '',
            model TEXT DEFAULT '',
            tokens INTEGER DEFAULT 0,
            metadata_json TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        """
    )
    conn.execute("INSERT INTO candidates VALUES (1, 'Java 后端简历', '{}')")
    conn.execute("INSERT INTO match_runs VALUES (100, 1)")
    conn.execute("INSERT INTO match_results VALUES (1000, 100)")
    conn.commit()
    return conn


def _fake_report():
    return {
        "candidate_id": 1,
        "candidate_name": "张三",
        "candidate": {"tags": "", "city": "北京"},
        "details": [
            {
                "title": "高级 Java 研发工程师",
                "company": "客户A",
                "final_score": 92,
                "score_summary": {"hard": {"score": 90}, "priority": {"score": 85}},
                "rows": [{"condition_type": "core", "requirement": "Java 后端", "label": "满足", "note": "经验匹配"}],
                "suggestions": [],
            }
        ],
    }


def test_client_recommendation_get_is_read_only(monkeypatch):
    conn = _conn()
    called = {"llm": False}
    monkeypatch.setattr(matching, "get_conn", lambda: conn)
    monkeypatch.setattr(matching, "_report_for_result", lambda _conn, _result_id: _fake_report())
    monkeypatch.setattr(matching.llm, "chat_json", lambda *args, **kwargs: called.update(llm=True))

    with pytest.raises(HTTPException) as exc:
        matching.get_client_recommendation(1000)

    assert exc.value.status_code == 404
    assert called["llm"] is False
    assert conn.execute("SELECT COUNT(*) AS c FROM client_recommendation_reports").fetchone()["c"] == 0


def test_client_recommendation_generate_uses_post_path(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(matching, "get_conn", lambda: conn)
    monkeypatch.setattr(matching, "_report_for_result", lambda _conn, _result_id: _fake_report())
    monkeypatch.setattr(
        matching.llm,
        "chat_json",
        lambda *args, **kwargs: (
            {
                "fields": {"推荐人选": "张三", "推荐岗位": "高级 Java 研发工程师"},
                "reasons": [{"title": "后端经验", "text": "Java 后端经验匹配。"}],
                "leave_reason": "待确认",
                "ps": "可推荐。",
            },
            "",
            123,
            0.1,
        ),
    )
    monkeypatch.setattr(matching.feishu_store, "push_client_recommendation", lambda *args, **kwargs: None)

    payload = matching.generate_client_recommendation(1000, {"id": 1, "role": "consultant"})

    assert payload["version"] == 1
    assert payload["is_ai"] is True
    assert payload["fields"]["推荐人选"] == "张三"
    assert conn.execute("SELECT COUNT(*) AS c FROM client_recommendation_reports").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) AS c FROM ai_usage_logs").fetchone()["c"] == 1
