import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SCHEMA
from app.services import report, report_export


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO companies (id, name) VALUES (1, '超线性')")
    conn.execute(
        "INSERT INTO candidates (id, name, resume_text) VALUES (1, '张三', '简历')"
    )
    for pid, seq, title in [(1, "35", "AI agent 算法工程师"), (2, "1", "游戏引擎研发"), (3, "44", "后端研发")]:
        conn.execute(
            "INSERT INTO positions (id, company_id, title, seq_no, jd_raw) VALUES (?,1,?,?,'jd')",
            (pid, title, seq),
        )
    conn.execute(
        "INSERT INTO match_runs (id, candidate_id, position_ids_json, status, model, total_tokens)"
        " VALUES (1, 1, '[1,2,3]', 'done', 'deepseek-v3', 1000)"
    )
    rows = [
        # 强匹配：96 分 → top
        (1, 1, "", 96, "top",
         json.dumps([{"requirement": "3年算法经验", "condition_type": "hard", "label": "满足", "note": "5年"},
                     {"requirement": "熟悉微调", "condition_type": "priority", "label": "待确认", "note": "未提及"}], ensure_ascii=False),
         json.dumps({"hard": {"total": 1, "satisfied": 1, "failed": 0, "pending": 0, "score": 100, "passed": True},
                     "priority": {"total": 1, "satisfied": 0, "failed": 0, "pending": 1, "score": 0},
                     "judgement": "可进入人工审核，需补充确认"}, ensure_ascii=False),
         json.dumps([{"type": "resume_add", "text": "补充微调经验"}], ensure_ascii=False)),
        # 不匹配：明细失败/未完成
        (1, 2, "明细分析失败", None, None, "", "", ""),
        # 边缘：88 分 → review
        (1, 3, "", 88, "review",
         json.dumps([{"requirement": "5年后端经验", "condition_type": "hard", "label": "不满足", "note": "仅3年"}], ensure_ascii=False),
         json.dumps({"hard": {"total": 1, "satisfied": 0, "failed": 1, "pending": 0, "score": 0, "passed": False},
                     "priority": {"total": 0, "satisfied": 0, "failed": 0, "pending": 0, "score": 0},
                     "judgement": "请重点关注，慎重推荐"}, ensure_ascii=False),
         json.dumps([{"type": "clarify_with_candidate", "text": "确认后端年限"}], ensure_ascii=False)),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO match_results (run_id, position_id, exclude_reason, final_score, recommend_level,"
            " detail_rows_json, dims_json, suggestions_json) VALUES (?,?,?,?,?,?,?,?)",
            r,
        )
    conn.commit()
    return conn


def test_report_structure():
    conn = make_db()
    run = conn.execute("SELECT * FROM match_runs WHERE id=1").fetchone()
    result = report.build_report(conn, run)
    assert result["candidate_name"] == "张三"
    assert "screen" not in result
    assert len(result["details"]) == 2
    detail_ids = {d["position_id"] for d in result["details"]}
    assert 2 not in detail_ids                   # 排除岗位不展开明细
    assert result["details"][0]["final_score"] == 96  # 按分数降序
    assert result["details"][0]["candidate_name"] == "张三"
    assert result["details"][0]["recommend_label"] == "重点推荐"
    assert result["details"][1]["recommend_label"] == "人工审核"
    assert "markdown" not in result


def test_internal_report_detail_export_title_includes_candidate_name():
    conn = make_db()
    run = conn.execute("SELECT * FROM match_runs WHERE id=1").fetchone()
    result = report.build_report(conn, run)

    assert (
        report_export._detail_title(result, result["details"][0])
        == "候选人：张三｜岗位35：AI agent 算法工程师（超线性）"
    )
