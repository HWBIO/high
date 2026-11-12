"""报告组装：匹配结果 → 结构化 JSON。纯 Python，无 LLM。"""
from ..db import company_display_expr, loads_or

RECOMMEND_LABELS = {"top": "重点推荐", "review": "人工审核", "caution": "慎重推荐"}

CONDITION_GROUPS = [
    ("blocker", "关键门槛", "风险/待确认点"),
    ("core", "核心能力", "风险/待确认点"),
    ("relevance", "相关经验", "亮点/缺口"),
    ("seniority", "资历/复杂度", "亮点/缺口"),
    ("preference", "客户偏好", "亮点/缺口"),
    ("risk", "风险可控度", "风险/待确认点"),
]

CONDITION_ALIASES = {"hard": "core", "priority": "preference", "soft": "preference"}


def _score_wording(text: str) -> str:
    return str(text or "").replace("匹配度", "匹配得分")


def _normalize_rows(rows: list) -> list:
    normalized = []
    for row in rows:
        normalized.append(
            {
                **row,
                "requirement": _score_wording(row.get("requirement", "")),
                "note": _score_wording(row.get("note", "")),
            }
        )
    return normalized


def _normalize_score_summary(summary: dict) -> dict:
    if not summary:
        return {}
    if "priority" not in summary and "soft" in summary:
        summary = {**summary, "priority": summary.get("soft")}
    return {
        **summary,
        "scoring_rule": _score_wording(summary.get("scoring_rule", "")),
    }


def _normalize_suggestions(suggestions: list) -> list:
    return [{**item, "text": _score_wording(item.get("text", ""))} for item in suggestions]


def build_report(conn, run) -> dict:
    company_expr = company_display_expr(conn, "c")
    candidate = conn.execute(
        "SELECT * FROM candidates WHERE id=?", (run["candidate_id"],)
    ).fetchone()
    rows = conn.execute(
        f"SELECT r.*, p.title, p.seq_no, {company_expr} AS company_name "
        "FROM match_results r "
        "JOIN positions p ON p.id = r.position_id "
        "LEFT JOIN companies c ON c.id = p.company_id "
        "WHERE r.run_id=? ORDER BY r.final_score DESC, r.position_id",
        (run["id"],),
    ).fetchall()

    details = []
    for r in rows:
        if r["final_score"] is not None:
            details.append(
                {
                    "position_id": r["position_id"],
                    "candidate_name": candidate["name"],
                    "seq_no": r["seq_no"],
                    "title": r["title"],
                    "company": r["company_name"] or "",
                    "final_score": r["final_score"],
                    "recommend_level": r["recommend_level"],
                    "recommend_label": RECOMMEND_LABELS.get(r["recommend_level"], ""),
                    "rows": _normalize_rows(loads_or(r["detail_rows_json"], [])),
                    "score_summary": _normalize_score_summary(loads_or(r["dims_json"], {})),
                    "suggestions": _normalize_suggestions(loads_or(r["suggestions_json"], [])),
                }
            )
    details.sort(key=lambda d: -d["final_score"])

    return {
        "run_id": run["id"],
        "candidate_id": candidate["id"],
        "candidate_name": candidate["name"],
        "candidate": {
            "id": candidate["id"],
            "name": candidate["name"],
            "phone": candidate["phone"],
            "city": candidate["city"],
            "current_salary": candidate["current_salary"],
            "expected_salary": candidate["expected_salary"],
            "tags": candidate["tags"],
            "target_position": candidate["target_position"],
            "current_company": candidate["current_company"],
            "current_title": candidate["current_title"],
            "age": candidate["age"],
            "talent_status": candidate["talent_status"],
            "owner": candidate["owner"],
            "has_resume": bool(candidate["resume_file"]),
        },
        "status": run["status"],
        "model": run["model"],
        "total_tokens": run["total_tokens"],
        "created_at": run["created_at"],
        "finished_at": run["finished_at"],
        "details": details,
    }
