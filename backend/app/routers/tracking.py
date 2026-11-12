"""候选人交付进程：候选人 × 客户岗位/项目的推进记录。

stage 为当前进程管理页面使用的单一阶段链：
    待推荐 → 初筛（已推荐客户） → 一面 → 二面 → 三面 → 四面 → 谈薪 → offer → 入职；
    任一阶段可转 pass。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..db import company_display_expr, get_conn
from ..services import feishu_store
from .auth import current_user, require_process_operator, require_user

router = APIRouter(prefix="/api/tracking", tags=["tracking"])

STAGES = (
    "待推荐",
    "初筛（已推荐客户）",
    "一面",
    "二面",
    "三面",
    "四面",
    "谈薪",
    "offer",
    "入职",
    "pass",
)

STAGE_ALIASES = {
    "已推荐客户": "初筛（已推荐客户）",
    "客户初筛通过": "初筛（已推荐客户）",
    "客户初筛未通过": "pass",
    "需补充信息": "初筛（已推荐客户）",
    "终面": "四面",
    "Offer": "offer",
    "OFFER": "offer",
    "淘汰": "pass",
    "Pass": "pass",
    "PASS": "pass",
}

REJECT_REASONS = (
    "硬性条件不符", "经验深度不够", "行业背景不符", "薪资不符", "城市/到岗不符",
    "稳定性顾虑", "简历表达不清", "客户HC变化", "候选人拒绝", "顾问放弃", "其他",
)

CLIENT_FEEDBACKS = (
    "待反馈", "感兴趣", "不感兴趣", "需要补充信息", "进入面试", "暂缓", "已拒绝",
)


def _derive_status(stage: str) -> str:
    if stage == "待推荐":
        return "未开始"
    if stage in ("入职",):
        return "已入职"
    if stage == "pass":
        return "已终止"
    return "进行中"


def _normalize_stage(stage: str) -> str:
    value = (stage or "待推荐").strip()
    return STAGE_ALIASES.get(value, value)


def _actor(conn, authorization: str) -> str:
    user = current_user(conn, authorization)
    return user["display_name"] if user else ""


def _log(conn, tracking_id: int, actor: str, action: str, field: str = "", old: str = "", new: str = "", note: str = ""):
    conn.execute(
        "INSERT INTO tracking_logs (tracking_id, actor, action, field, old_value, new_value, note)"
        " VALUES (?,?,?,?,?,?,?)",
        (tracking_id, actor, action, field, old, new, note),
    )


class TrackingIn(BaseModel):
    candidate_id: int
    position_id: Optional[int] = None
    match_result_id: Optional[int] = None
    final_score_snapshot: Optional[float] = None
    recommend_level_snapshot: str = ""
    source: str = ""
    recommended_at: str = ""
    interview_at: str = ""
    owner: str = ""
    stage: str = "初筛（已推荐客户）"
    client_feedback: str = "待反馈"
    next_action: str = ""


class TrackingUpdate(BaseModel):
    stage: Optional[str] = None
    reject_reason: Optional[str] = None
    client_feedback: Optional[str] = None
    next_action: Optional[str] = None
    position_id: Optional[int] = None
    match_result_id: Optional[int] = None
    final_score_snapshot: Optional[float] = None
    recommend_level_snapshot: Optional[str] = None
    source: Optional[str] = None
    recommended_at: Optional[str] = None
    interview_at: Optional[str] = None
    owner: Optional[str] = None


def _field_was_sent(body: BaseModel, field: str) -> bool:
    return field in getattr(body, "model_fields_set", getattr(body, "__fields_set__", set()))


def _hydrate_tracking_score(conn, row: dict) -> None:
    if row.get("final_score_snapshot") is not None or not row.get("position_id"):
        return
    latest = conn.execute(
        "SELECT mr.id, mr.final_score, mr.recommend_level"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " WHERE r.candidate_id=? AND mr.position_id=? AND mr.final_score IS NOT NULL"
        " ORDER BY mr.id DESC LIMIT 1",
        (row["candidate_id"], row["position_id"]),
    ).fetchone()
    if not latest:
        return
    row["match_result_id"] = row.get("match_result_id") or latest["id"]
    row["final_score_snapshot"] = latest["final_score"]
    row["recommend_level_snapshot"] = row.get("recommend_level_snapshot") or latest["recommend_level"] or ""


@router.get("/init")
def tracking_init():
    conn = get_conn()
    company_expr = company_display_expr(conn, "c")
    users = conn.execute(
        "SELECT id, username, display_name, role, status FROM users"
        " WHERE status='active' AND role IN ('business_admin','consultant')"
        " ORDER BY id"
    ).fetchall()
    positions = conn.execute(
        f"SELECT p.id, p.seq_no, p.title, {company_expr} AS company"
        " FROM positions p LEFT JOIN companies c ON c.id=p.company_id"
        " WHERE p.status='active'"
        " ORDER BY CAST(p.seq_no AS INTEGER), p.id"
    ).fetchall()
    return {
        "meta": {"stages": STAGES, "reject_reasons": REJECT_REASONS, "client_feedbacks": CLIENT_FEEDBACKS},
        "users": [dict(row) for row in users],
        "positions": [dict(row) for row in positions],
    }


@router.get("")
def list_tracking(_user=Depends(require_user)):
    conn = get_conn()
    company_expr = company_display_expr(conn, "co")
    rows = conn.execute(
        "SELECT t.*, c.name AS candidate_name, p.title AS position_title, p.seq_no,"
        f" {company_expr} AS company"
        " FROM candidate_tracking t"
        " JOIN candidates c ON c.id = t.candidate_id"
        " LEFT JOIN positions p ON p.id = t.position_id"
        " LEFT JOIN companies co ON co.id = p.company_id"
        " ORDER BY t.id DESC"
    ).fetchall()
    result = [dict(r) for r in rows]
    for row in result:
        row["stage"] = _normalize_stage(row.get("stage") or "")
        _hydrate_tracking_score(conn, row)
    return result


@router.get("/logs")
def list_tracking_logs(limit: int = 100, _user=Depends(require_user)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT l.*, c.name AS candidate_name, p.title AS position_title"
        " FROM tracking_logs l"
        " LEFT JOIN candidate_tracking t ON t.id=l.tracking_id"
        " LEFT JOIN candidates c ON c.id=t.candidate_id"
        " LEFT JOIN positions p ON p.id=t.position_id"
        " ORDER BY l.id DESC LIMIT ?",
        (min(max(limit, 1), 500),),
    ).fetchall()
    return [dict(r) for r in rows]


def _pct(num: int, den: int):
    return round(num * 100 / den, 1) if den else None


@router.get("/analytics")
def tracking_analytics(
    company: str = "",
    date_from: str = "",
    date_to: str = "",
    position_id: Optional[int] = None,
    owner: str = "",
    _user=Depends(require_user),
):
    """招聘转化分析报告：基于当前状态看板快照生成第一版漏斗/岗位/顾问统计。"""
    conn = get_conn()
    company_expr = company_display_expr(conn, "c")
    tracking_company_expr = company_display_expr(conn, "co")
    option_company_where = [f"{company_expr} IS NOT NULL", f"{company_expr}!=''"]
    options = {
        "companies": [
            r["name"] for r in conn.execute(
                f"SELECT DISTINCT {company_expr} AS name FROM candidate_tracking t"
                " JOIN positions p ON p.id=t.position_id"
                " JOIN companies c ON c.id=p.company_id"
                f" WHERE {' AND '.join(option_company_where)} ORDER BY name",
            ).fetchall()
        ],
        "owners": [
            r["owner"] for r in conn.execute(
                "SELECT DISTINCT owner FROM candidate_tracking WHERE owner!='' ORDER BY owner",
            ).fetchall()
        ],
        "positions": [
            dict(r) for r in conn.execute(
                f"SELECT DISTINCT p.id, p.seq_no, p.title, {company_expr} AS company"
                " FROM candidate_tracking t JOIN positions p ON p.id=t.position_id"
                " LEFT JOIN companies c ON c.id=p.company_id"
                " ORDER BY CAST(p.seq_no AS INTEGER), p.id",
            ).fetchall()
        ],
    }

    clauses, params = [], []
    if company.strip():
        clauses.append(f"{tracking_company_expr}=?")
        params.append(company.strip())
    if date_from.strip():
        clauses.append("date(t.created_at) >= date(?)")
        params.append(date_from.strip())
    if date_to.strip():
        clauses.append("date(t.created_at) <= date(?)")
        params.append(date_to.strip())
    if position_id is not None:
        clauses.append("t.position_id=?")
        params.append(position_id)
    if owner.strip():
        clauses.append("t.owner=?")
        params.append(owner.strip())
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = [
        dict(r) for r in conn.execute(
            "SELECT t.*, c.name AS candidate_name, p.title AS position_title, p.seq_no,"
            f" {tracking_company_expr} AS company FROM candidate_tracking t"
            " JOIN candidates c ON c.id=t.candidate_id"
            " LEFT JOIN positions p ON p.id=t.position_id"
            " LEFT JOIN companies co ON co.id=p.company_id "
            f"{where} ORDER BY t.id DESC",
            params,
        ).fetchall()
    ]
    for row in rows:
        row["stage"] = _normalize_stage(row.get("stage") or "")

    stage_order = {stage: idx for idx, stage in enumerate(STAGES)}
    funnel_stages = ("初筛（已推荐客户）", "一面", "二面", "三面", "四面", "谈薪", "offer", "入职")
    total = len(rows)

    def reached(stage):
        idx = stage_order[stage]
        return sum(1 for r in rows if r["stage"] != "pass" and stage_order.get(r["stage"], -1) >= idx)

    funnel = []
    previous = total
    for stage in funnel_stages:
        count = reached(stage)
        funnel.append({
            "stage": stage,
            "count": count,
            "stage_rate": _pct(count, previous),
            "total_rate": _pct(count, total),
        })
        previous = count

    reject_total = sum(1 for r in rows if r["stage"] == "pass")
    reject_map = {}
    for r in rows:
        if r["stage"] == "pass":
            reason = r["reject_reason"] or "未填写"
            reject_map[reason] = reject_map.get(reason, 0) + 1
    reject_reasons = [
        {"reason": reason, "count": count, "rate": _pct(count, reject_total)}
        for reason, count in sorted(reject_map.items(), key=lambda item: item[1], reverse=True)
    ]

    def group_stats(key_fn):
        grouped = {}
        for r in rows:
            key = key_fn(r)
            item = grouped.setdefault(key, {"name": key, "total": 0, "screen": 0, "first": 0, "second": 0, "offer": 0, "hired": 0, "in_progress": 0, "rejected": 0})
            item["total"] += 1
            item["screen"] += 1 if r["stage"] != "pass" and stage_order.get(r["stage"], -1) >= stage_order["初筛（已推荐客户）"] else 0
            item["first"] += 1 if r["stage"] != "pass" and stage_order.get(r["stage"], -1) >= stage_order["一面"] else 0
            item["second"] += 1 if r["stage"] != "pass" and stage_order.get(r["stage"], -1) >= stage_order["二面"] else 0
            item["offer"] += 1 if r["stage"] != "pass" and stage_order.get(r["stage"], -1) >= stage_order["offer"] else 0
            item["hired"] += 1 if r["stage"] != "pass" and stage_order.get(r["stage"], -1) >= stage_order["入职"] else 0
            item["rejected"] += 1 if r["stage"] == "pass" else 0
            item["in_progress"] += 1 if _derive_status(r["stage"]) == "进行中" else 0
        for item in grouped.values():
            item["screen_rate"] = _pct(item["screen"], item["total"])
            item["first_rate"] = _pct(item["first"], item["screen"])
            item["offer_rate"] = _pct(item["offer"], item["total"])
            item["hired_rate"] = _pct(item["hired"], item["total"])
        return sorted(grouped.values(), key=lambda item: item["total"], reverse=True)

    position_stats = group_stats(lambda r: (f"岗位{r['seq_no']}：" if r["seq_no"] else "") + (r["position_title"] or "未选岗位"))
    owner_stats = group_stats(lambda r: r["owner"] or "未指定")
    findings = []
    if total:
        findings.append(f"本次筛选范围内共有 {total} 条跟进记录，offer {reached('offer')} 人，入职 {reached('入职')} 人。")
        if funnel:
            findings.append(f"初筛推进率为 {_pct(reached('初筛（已推荐客户）'), total) or 0}%，一面及以后转化率为 {_pct(reached('一面'), total) or 0}%。")
        if reject_reasons:
            top = reject_reasons[0]
            findings.append(f"主要淘汰原因是「{top['reason']}」，占淘汰记录的 {top['rate'] or 0}%。")
        if position_stats:
            low = [p for p in position_stats if p["total"] >= 3 and (p["screen_rate"] or 0) < 40]
            if low:
                findings.append(f"需要重点复盘岗位：{low[0]['name']}，推荐量 {low[0]['total']}，初筛推进率 {low[0]['screen_rate']}%。")
    else:
        findings.append("当前筛选条件下暂无可分析的跟进记录。")

    return {
        "filters": {"company": company, "date_from": date_from, "date_to": date_to, "position_id": position_id, "owner": owner},
        "options": options,
        "summary": {"total": total, "offer": reached("offer"), "hired": reached("入职"), "rejected": reject_total, "in_progress": sum(1 for r in rows if _derive_status(r["stage"]) == "进行中")},
        "funnel": funnel,
        "reject_reasons": reject_reasons,
        "position_stats": position_stats,
        "owner_stats": owner_stats,
        "findings": findings,
    }


@router.post("")
def create_tracking(
    body: TrackingIn,
    authorization: str = Header(default=""),
    user=Depends(require_process_operator),
):
    conn = get_conn()
    stage = _normalize_stage(body.stage)
    if stage not in STAGES:
        raise HTTPException(400, f"stage 取值必须是 {STAGES}")
    if body.client_feedback not in CLIENT_FEEDBACKS:
        raise HTTPException(400, f"client_feedback 取值必须是 {CLIENT_FEEDBACKS}")
    cand = conn.execute(
        "SELECT id FROM candidates WHERE id=?", (body.candidate_id,)
    ).fetchone()
    if not cand:
        raise HTTPException(404, "候选人不存在")
    dup = conn.execute(
        "SELECT * FROM candidate_tracking WHERE candidate_id=? AND position_id IS ?",
        (body.candidate_id, body.position_id),
    ).fetchone()
    if dup:
        fields, params = [], []
        if body.match_result_id is not None:
            fields.append("match_result_id=?")
            params.append(body.match_result_id)
        if body.final_score_snapshot is not None:
            fields.append("final_score_snapshot=?")
            params.append(body.final_score_snapshot)
        if body.recommend_level_snapshot.strip():
            fields.append("recommend_level_snapshot=?")
            params.append(body.recommend_level_snapshot.strip())
        if body.source.strip():
            fields.append("source=?")
            params.append(body.source.strip())
        if fields:
            params.append(dup["id"])
            conn.execute(
                f"UPDATE candidate_tracking SET {', '.join(fields)}, updated_at=datetime('now','localtime') WHERE id=?",
                params,
            )
            _log(conn, dup["id"], _actor(conn, authorization), "update", note="更新匹配报告快照")
            conn.commit()
            feishu_store.push_tracking(conn, dup["id"])
        return {"id": dup["id"], "existed": True}
    cur = conn.execute(
        "INSERT INTO candidate_tracking (candidate_id, position_id, match_result_id, final_score_snapshot,"
        " recommend_level_snapshot, source, recommended_at, interview_at, owner, stage, client_feedback, next_action)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            body.candidate_id,
            body.position_id,
            body.match_result_id,
            body.final_score_snapshot,
            body.recommend_level_snapshot.strip(),
            body.source.strip(),
            body.recommended_at.strip(),
            body.interview_at.strip(),
            body.owner.strip() or user["display_name"],
            stage,
            body.client_feedback,
            body.next_action.strip(),
        ),
    )
    _log(conn, cur.lastrowid, _actor(conn, authorization), "create", note="创建跟进记录")
    conn.commit()
    feishu_store.push_tracking(conn, cur.lastrowid)
    return {"id": cur.lastrowid, "existed": False}


@router.put("/{tracking_id}")
def update_tracking(
    tracking_id: int,
    body: TrackingUpdate,
    authorization: str = Header(default=""),
    user=Depends(require_process_operator),
):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM candidate_tracking WHERE id=?", (tracking_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "跟进记录不存在")
    fields, params = [], []
    changes = []
    if body.stage is not None:
        stage = _normalize_stage(body.stage)
        if stage not in STAGES:
            raise HTTPException(400, f"stage 取值必须是 {STAGES}")
        fields.append("stage=?")
        params.append(stage)
        if stage != _normalize_stage(row["stage"]):
            changes.append(("stage", row["stage"], stage))
        if stage != "pass" and body.reject_reason is None:
            fields.append("reject_reason=?")
            params.append("")
    if body.reject_reason is not None:
        if body.reject_reason and body.reject_reason not in REJECT_REASONS:
            raise HTTPException(400, f"reject_reason 取值必须是 {REJECT_REASONS}")
        fields.append("reject_reason=?")
        params.append(body.reject_reason)
        if body.reject_reason != row["reject_reason"]:
            changes.append(("reject_reason", row["reject_reason"] or "", body.reject_reason or ""))
    if body.client_feedback is not None:
        if body.client_feedback and body.client_feedback not in CLIENT_FEEDBACKS:
            raise HTTPException(400, f"client_feedback 取值必须是 {CLIENT_FEEDBACKS}")
        fields.append("client_feedback=?")
        params.append(body.client_feedback)
        if body.client_feedback != (row["client_feedback"] or ""):
            changes.append(("client_feedback", row["client_feedback"] or "", body.client_feedback or ""))
    if body.next_action is not None:
        fields.append("next_action=?")
        params.append(body.next_action.strip())
        if body.next_action.strip() != (row["next_action"] or ""):
            changes.append(("next_action", row["next_action"] or "", body.next_action.strip()))
    if body.owner is not None:
        fields.append("owner=?")
        params.append(body.owner.strip())
        if body.owner.strip() != (row["owner"] or ""):
            changes.append(("owner", row["owner"] or "", body.owner.strip()))
    if _field_was_sent(body, "position_id"):
        fields.append("position_id=?")
        params.append(body.position_id)
        position_changed = body.position_id != row["position_id"]
        if position_changed:
            changes.append(("position_id", str(row["position_id"] or ""), str(body.position_id)))
            if not _field_was_sent(body, "match_result_id"):
                fields.append("match_result_id=?")
                params.append(None)
                changes.append(("match_result_id", str(row["match_result_id"] or ""), ""))
            if not _field_was_sent(body, "final_score_snapshot"):
                fields.append("final_score_snapshot=?")
                params.append(0)
                changes.append(("final_score_snapshot", str(row["final_score_snapshot"] or ""), "0"))
            if body.recommend_level_snapshot is None:
                fields.append("recommend_level_snapshot=?")
                params.append("")
                changes.append(("recommend_level_snapshot", row["recommend_level_snapshot"] or "", ""))
    for field in ("match_result_id", "final_score_snapshot"):
        if _field_was_sent(body, field):
            fields.append(f"{field}=?")
            value = getattr(body, field)
            params.append(value)
            if value != row[field]:
                changes.append((field, str(row[field] or ""), str(value or "")))
    for field in ("recommend_level_snapshot", "source", "recommended_at", "interview_at"):
        value = getattr(body, field)
        if value is not None:
            clean = value.strip()
            fields.append(f"{field}=?")
            params.append(clean)
            if clean != (row[field] or ""):
                changes.append((field, row[field] or "", clean))
    if not fields:
        return {"updated": False}
    params.append(tracking_id)
    conn.execute(
        f"UPDATE candidate_tracking SET {', '.join(fields)},"
        " updated_at=datetime('now','localtime') WHERE id=?",
        params,
    )
    actor = _actor(conn, authorization)
    for field, old, new in changes:
        _log(conn, tracking_id, actor, "update", field, old, new)
    conn.commit()
    feishu_store.push_tracking(conn, tracking_id)
    return {"updated": True}


@router.delete("/{tracking_id}")
def delete_tracking(
    tracking_id: int,
    authorization: str = Header(default=""),
    user=Depends(require_process_operator),
):
    conn = get_conn()
    row = conn.execute("SELECT * FROM candidate_tracking WHERE id=?", (tracking_id,)).fetchone()
    if not row:
        raise HTTPException(404, "跟进记录不存在")
    _log(conn, tracking_id, _actor(conn, authorization), "delete", note="删除跟进记录")
    conn.execute("DELETE FROM candidate_tracking WHERE id=?", (tracking_id,))
    conn.commit()
    if row:
        feishu_store.delete_tracking(row["feishu_record_id"])
    return {"deleted": True}
