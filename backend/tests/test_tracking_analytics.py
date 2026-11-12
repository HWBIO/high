import sqlite3

from app.routers import tracking


def test_tracking_analytics_company_filter_uses_display_name(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            legal_name TEXT DEFAULT '',
            short_name TEXT DEFAULT ''
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            company_id INTEGER,
            seq_no TEXT DEFAULT '',
            title TEXT DEFAULT ''
        );
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            name TEXT DEFAULT ''
        );
        CREATE TABLE candidate_tracking (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER,
            position_id INTEGER,
            match_result_id INTEGER,
            final_score_snapshot REAL,
            recommend_level_snapshot TEXT DEFAULT '',
            source TEXT DEFAULT '',
            recommended_at TEXT DEFAULT '',
            interview_at TEXT DEFAULT '',
            stage TEXT DEFAULT '初筛（已推荐客户）',
            reject_reason TEXT DEFAULT '',
            client_feedback TEXT DEFAULT '待反馈',
            next_action TEXT DEFAULT '',
            owner TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );
        """
    )
    conn.execute(
        "INSERT INTO companies (id, name, legal_name, short_name) VALUES (1, 'yu', '云起无垠', '')"
    )
    conn.execute("INSERT INTO positions (id, company_id, seq_no, title) VALUES (10, 1, '1', '后端工程师')")
    conn.execute("INSERT INTO candidates (id, name) VALUES (100, '张三')")
    conn.execute(
        "INSERT INTO candidate_tracking"
        " (id, candidate_id, position_id, final_score_snapshot, stage, owner)"
        " VALUES (1000, 100, 10, 88, '初筛（已推荐客户）', '顾问A')"
    )
    conn.commit()
    monkeypatch.setattr(tracking, "get_conn", lambda: conn)

    result = tracking.tracking_analytics(company="云起无垠", _user={"id": 1})

    assert result["options"]["companies"] == ["云起无垠"]
    assert result["summary"]["total"] == 1
