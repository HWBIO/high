import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SCHEMA
from app.routers import matching
from app.services import matcher


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO candidates (id, name, resume_text) VALUES (1, '候选人', '简历正文')")
    conn.execute(
        "INSERT INTO match_runs (id, candidate_id, position_ids_json, status)"
        " VALUES (100, 1, ?, 'pending')",
        (json.dumps([10, 11]),),
    )
    conn.commit()
    return conn


def _stub_common(monkeypatch, conn, detail_one):
    positions = [
        {"id": 10, "title": "岗位A"},
        {"id": 11, "title": "岗位B"},
    ]
    monkeypatch.setattr(matcher, "get_conn", lambda: conn)
    monkeypatch.setattr(matcher, "_load_positions", lambda _conn, _ids: positions)
    monkeypatch.setattr(
        matcher.prompt_composer,
        "resolve_active_prompt",
        lambda *_args, **_kwargs: {"content": "通用提示词"},
    )
    monkeypatch.setattr(matcher.prompt_composer, "compose_system", lambda *_args: "system")
    monkeypatch.setattr(matcher, "_detail_one", detail_one)
    monkeypatch.setattr(matcher.feishu_store, "push_match_result", lambda *_args, **_kwargs: None)


def _detail_result(position_id: int) -> dict:
    return {
        "position_id": position_id,
        "final_score": 88,
        "recommend_level": "review",
        "rows": [{"requirement": "核心能力", "condition_type": "core", "label": "满足", "note": "匹配"}],
        "dims": {"judgement": "可进入人工审核"},
        "suggestions": [],
        "raw": "{}",
        "input_signature": "sig",
        "reused_from_result_id": None,
        "tokens": 0,
        "latency_ms": 0,
        "user_prompt": "user prompt",
    }


def test_execute_run_marks_failed_when_all_details_fail(monkeypatch):
    conn = _conn()

    def fail_detail(*_args, **_kwargs):
        raise RuntimeError("LLM timeout")

    _stub_common(monkeypatch, conn, fail_detail)

    matcher.execute_run(100)

    run = conn.execute("SELECT status, error, finished_at FROM match_runs WHERE id=100").fetchone()
    assert run["status"] == "failed"
    assert "全部岗位明细分析失败" in run["error"]
    assert run["finished_at"]
    progress = matcher.run_progress(conn, conn.execute("SELECT * FROM match_runs WHERE id=100").fetchone())
    assert progress["results_saved"] == 2
    assert progress["valid_results"] == 0
    assert progress["failed_results"] == 2


def test_execute_run_marks_done_with_errors_when_some_details_fail(monkeypatch):
    conn = _conn()

    def partial_detail(position, *_args, **_kwargs):
        if position["id"] == 11:
            raise RuntimeError("LLM timeout")
        return _detail_result(position["id"])

    _stub_common(monkeypatch, conn, partial_detail)

    matcher.execute_run(100)

    run = conn.execute("SELECT status, error, finished_at FROM match_runs WHERE id=100").fetchone()
    assert run["status"] == "done_with_errors"
    assert "1/2 个岗位明细分析失败" in run["error"]
    assert run["finished_at"]
    progress = matcher.run_progress(conn, conn.execute("SELECT * FROM match_runs WHERE id=100").fetchone())
    assert progress["results_saved"] == 2
    assert progress["valid_results"] == 1
    assert progress["failed_results"] == 1


def test_batch_status_counts_done_without_valid_score_as_failed(monkeypatch):
    conn = _conn()
    conn.execute("UPDATE match_runs SET status='done' WHERE id=100")
    conn.execute("INSERT INTO companies (id, name) VALUES (1, '客户')")
    conn.execute("INSERT INTO positions (id, company_id, title, jd_raw) VALUES (10, 1, '岗位A', 'jd')")
    conn.execute("INSERT INTO positions (id, company_id, title, jd_raw) VALUES (11, 1, '岗位B', 'jd')")
    conn.execute(
        "INSERT INTO match_results (run_id, position_id, exclude_reason, final_score)"
        " VALUES (100, 10, '明细分析失败', NULL)"
    )
    conn.commit()
    monkeypatch.setattr(matching, "get_conn", lambda: conn)

    summary = matching.get_runs_status(matching.BatchStatusIn(run_ids=[100]))

    assert summary["done"] == 0
    assert summary["failed"] == 1
    assert summary["running"] == 0
