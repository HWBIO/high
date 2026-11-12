import sqlite3

from app.routers import matching


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            name TEXT,
            resume_hash TEXT DEFAULT ''
        );
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            title TEXT,
            seq_no TEXT,
            company_id INTEGER
        );
        CREATE TABLE match_runs (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER,
            created_by_user_id INTEGER,
            status TEXT,
            total_tokens INTEGER,
            created_at TEXT,
            finished_at TEXT,
            error TEXT
        );
        CREATE TABLE match_results (
            id INTEGER PRIMARY KEY,
            run_id INTEGER,
            position_id INTEGER,
            final_score REAL,
            recommend_level TEXT,
            exclude_reason TEXT,
            review_status TEXT
        );
        """
    )
    conn.execute("INSERT INTO candidates (id, name, resume_hash) VALUES (1, '张三', 'hash-a')")
    conn.execute("INSERT INTO candidates (id, name, resume_hash) VALUES (2, '李四', '')")
    conn.execute("INSERT INTO companies VALUES (1, '客户A')")
    conn.execute("INSERT INTO positions VALUES (10, 'Java后端', '1', 1)")
    conn.execute("INSERT INTO positions VALUES (11, '前端研发', '2', 1)")
    conn.execute("INSERT INTO match_runs VALUES (100, 1, 1, 'done', 1000, '2026-08-25 10:00:00', '', '')")
    conn.execute("INSERT INTO match_runs VALUES (101, 2, 1, 'done', 1000, '2026-08-25 11:00:00', '', '')")
    conn.execute("INSERT INTO match_results VALUES (1000, 100, 10, 85, 'strong', '', 'pending')")
    conn.execute("INSERT INTO match_results VALUES (1001, 101, 11, 60, 'weak', '', 'pending')")
    conn.commit()
    return conn


def _ids(page):
    return [item["result_id"] for item in page["items"]]


def test_history_results_filters_by_position_candidate_and_score(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(matching, "get_conn", lambda: conn)

    assert _ids(matching.list_history_results(position_id=10)) == [1000]
    assert _ids(matching.list_history_results(candidate_name="李")) == [1001]
    assert _ids(matching.list_history_results(score_band="review")) == [1000]
    assert _ids(matching.list_history_results(score_band="no_review")) == [1001]


def test_history_results_are_company_shared(monkeypatch):
    conn = _conn()
    conn.execute("INSERT INTO candidates (id, name, resume_hash) VALUES (3, '王五', 'hash-a')")
    conn.execute("INSERT INTO match_runs VALUES (102, 3, 2, 'done', 1000, '2026-08-25 12:00:00', '', '')")
    conn.execute("INSERT INTO match_results VALUES (1002, 102, 10, 90, 'strong', '', 'pending')")
    conn.commit()
    monkeypatch.setattr(matching, "get_conn", lambda: conn)

    assert _ids(matching.list_history_results(user={"id": 1})) == [1002, 1001, 1000]
    assert _ids(matching.list_history_results(user={"id": 2})) == [1002, 1001, 1000]
