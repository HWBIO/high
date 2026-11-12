import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.background import BackgroundTask
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..config import settings
from ..db import company_display_expr, ensure_chat_schema, get_conn, loads_or
from ..services import ai_usage, feishu_store, llm, match_queue, match_result_cleanup, matcher, report, report_export, resume_parser, temp_data
from .auth import require_ai_operator, require_client_report_operator, require_match_operator, require_report_reader

router = APIRouter(prefix="/api/match", tags=["matching"])


def _delete_temp_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _export_file_response(path: Path, filename: str) -> FileResponse:
    return FileResponse(
        path,
        filename=filename,
        background=BackgroundTask(_delete_temp_file, path),
    )


@router.get("/config")
def get_match_config():
    """匹配相关阈值，前端展示与判断统一从这里取，避免多处硬编码。"""
    return {
        "review_threshold": settings.review_threshold,
        "top_threshold": settings.top_threshold,
    }


class BatchStatusIn(BaseModel):
    run_ids: list[int]


class MatchSummaryIn(BaseModel):
    run_ids: list[int]
    result_id: Optional[int] = None
    result_ids: list[int] = []
    selected_result_ids: list[int] = []


class MatchWorkspaceStateIn(BaseModel):
    run_ids: list[int]
    result_id: Optional[int] = None
    result_ids: list[int] = []
    added_result_ids: list[int] = []
    hidden_result_ids: list[int] = []


class MatchChatMessage(BaseModel):
    role: str
    content: str


class MatchChatIn(BaseModel):
    run_ids: list[int]
    message: str
    history: list[MatchChatMessage] = []
    result_id: Optional[int] = None
    result_ids: list[int] = []
    selected_result_ids: list[int] = []


class ClientRecommendationIn(BaseModel):
    fields: dict[str, str]
    reasons: list[dict[str, str]]
    leave_reason: str = ""
    ps: str = ""
    status: str = "草稿"


AUTO_SUMMARY_PREFIX = "本次匹配综述："
SUCCESS_RUN_STATUSES = {"done", "done_with_errors"}
TERMINAL_RUN_STATUSES = {*SUCCESS_RUN_STATUSES, "failed"}


def _current_user_id(user) -> Optional[int]:
    try:
        return int(user["id"])
    except Exception:  # noqa: BLE001 测试直接调用路由函数时可能没有真实 Depends 用户
        return None


def _parse_ids_csv(value: str = "") -> list[int]:
    return list(dict.fromkeys(int(x) for x in value.split(",") if x.strip().isdigit()))


def _match_runs_has_owner(conn) -> bool:
    try:
        return any(row["name"] == "created_by_user_id" for row in conn.execute("PRAGMA table_info(match_runs)").fetchall())
    except Exception:  # noqa: BLE001
        return False


def _owned_result_exists(conn, result_id: int, user) -> bool:
    row = conn.execute(
        "SELECT 1"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " WHERE mr.id=?",
        (result_id,),
    ).fetchone()
    return bool(row)


def _ensure_owned_result(conn, result_id: int, user) -> None:
    if not _owned_result_exists(conn, result_id, user):
        raise HTTPException(404, "匹配结果不存在")


def _owned_result_ids(conn, result_ids: list[int], user) -> list[int]:
    result_ids = list(dict.fromkeys(int(item) for item in result_ids if item))
    if not result_ids:
        return result_ids
    placeholders = ",".join(["?"] * len(result_ids))
    rows = conn.execute(
        "SELECT mr.id"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        f" WHERE mr.id IN ({placeholders})",
        result_ids,
    ).fetchall()
    owned = {row["id"] for row in rows}
    return [item for item in result_ids if item in owned]


def _fallback_client_recommendation(data: dict) -> dict:
    detail = (data.get("details") or [{}])[0]
    candidate = data.get("candidate") or {}
    rows = detail.get("rows") or []
    matched = [row for row in rows if row.get("label") in ("满足", "完全匹配")]
    reasons = []
    for row in matched[:3]:
        text = row.get("note") or row.get("requirement") or ""
        if text:
            reasons.append({"title": row.get("requirement") or "匹配理由", "text": text})
    if not reasons:
        reasons.append({
            "title": "匹配基础",
            "text": "候选人与目标岗位存在一定匹配基础，建议顾问结合简历附件继续人工核对。",
        })
    return {
        "fields": {
            "推荐人选": data.get("candidate_name") or "",
            "推荐岗位": detail.get("title") or "",
            "性别": "待确认",
            "年龄": candidate.get("age") or "待确认",
            "学历": "待确认",
            "期望地点": candidate.get("city") or "待确认",
            "目前状态": candidate.get("talent_status") or "待确认",
            "目前薪酬": candidate.get("current_salary") or "待确认",
            "期望薪酬": candidate.get("expected_salary") or "待确认",
            "用工形式": "待确认",
        },
        "reasons": reasons[:4],
        "leave_reason": "待顾问确认补充",
        "ps": "如候选人有简历暂不便更新、JD 条款已线下确认等信息，请顾问在发送前补充。",
        "model": "",
        "tokens": 0,
        "is_ai": False,
    }


def _client_recommendation_text(payload: dict) -> str:
    fields = payload.get("fields") or {}
    reasons = payload.get("reasons") or []
    lines = [f"{fields.get('推荐人选') or '候选人'} 推荐报告", ""]
    for label in ("推荐人选", "推荐岗位", "性别", "年龄", "学历", "期望地点", "目前状态", "目前薪酬", "期望薪酬", "用工形式"):
        lines.append(f"{label}：{fields.get(label) or '待确认'}")
    lines.extend(["", "推荐理由", ""])
    for index, item in enumerate(reasons, 1):
        title = str(item.get("title") or "推荐理由").strip()
        text = str(item.get("text") or "").strip()
        lines.append(f"{index}. {title}：{text}")
    lines.extend(["", "离职原因", "", payload.get("leave_reason") or "待顾问确认补充", "", "PS", "", payload.get("ps") or ""])
    return "\n".join(lines).strip() + "\n"


def _client_recommendation_from_row(row) -> dict:
    payload = loads_or(row["content_json"], {})
    payload.setdefault("fields", {})
    payload.setdefault("reasons", [])
    payload.setdefault("leave_reason", "")
    payload.setdefault("ps", "")
    return {
        **payload,
        "id": row["id"],
        "version": row["version"],
        "status": row["status"],
        "model": row["model"],
        "tokens": row["tokens"],
        "is_ai": bool(row["model"]),
    }


def _save_client_recommendation(conn, result_id: int, payload: dict, model: str = "", tokens: int = 0, status: str = "草稿") -> dict:
    current = conn.execute(
        "SELECT MAX(version) AS v FROM client_recommendation_reports WHERE result_id=?",
        (result_id,),
    ).fetchone()
    version = int((current["v"] or 0) + 1)
    body_text = _client_recommendation_text(payload)
    cur = conn.execute(
        "INSERT INTO client_recommendation_reports"
        " (result_id, version, content_json, markdown, model, tokens, status)"
        " VALUES (?,?,?,?,?,?,?)",
        (result_id, version, json.dumps(payload, ensure_ascii=False), body_text, model, tokens, status or "草稿"),
    )
    conn.commit()
    feishu_store.push_client_recommendation(conn, cur.lastrowid)
    row = conn.execute("SELECT * FROM client_recommendation_reports WHERE id=?", (cur.lastrowid,)).fetchone()
    return _client_recommendation_from_row(row)


def _create_run_for_candidate(conn, candidate_id: int, position_ids: list[int], user_id: Optional[int]) -> int:
    cur = conn.execute(
        "INSERT INTO match_runs (candidate_id, created_by_user_id, position_ids_json) VALUES (?,?,?)",
        (candidate_id, user_id, json.dumps(position_ids)),
    )
    return cur.lastrowid


def _page(limit: int = 50, offset: int = 0) -> tuple[int, int]:
    return max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))


async def _save_temp_candidate(conn, file: UploadFile, user_id: Optional[int]) -> dict:
    suffix = (file.filename or "").split(".")[-1].lower()
    dest = settings.upload_dir / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.{suffix or 'bin'}"
    raw = await file.read()
    resume_hash = hashlib.sha256(raw).hexdigest()
    dest.write_bytes(raw)
    try:
        parsed = resume_parser.parse_resume_file(dest, file.filename or dest.name)
    except Exception:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    cur = conn.execute(
        "INSERT INTO candidates"
        " (name, resume_file, resume_hash, resume_text, resume_struct_json, talent_status, created_by_user_id, is_temp)"
        " VALUES (?,?,?,?,?,?,?,1)",
        (
            parsed["name"],
            str(dest),
            resume_hash,
            resume_parser.sections_to_text(parsed["sections"]),
            json.dumps(parsed["sections"], ensure_ascii=False),
            "临时匹配",
            user_id,
        ),
    )
    return {
        "candidate_id": cur.lastrowid,
        "name": parsed["name"],
        "filename": file.filename,
        "resume_hash": resume_hash,
        "warnings": parsed["warnings"],
    }


@router.post("/batch-runs")
async def create_batch_runs(
    position_ids_json: str = Form(...),
    files: list[UploadFile] = File(...),
    user=Depends(require_match_operator),
):
    """新版发起匹配：临时上传简历 × 已选项目，生成 n*m 个独立匹配任务。"""
    if not files:
        raise HTTPException(400, "请至少上传 1 份简历")
    if len(files) > 10:
        raise HTTPException(400, "一次最多上传 10 份简历")
    try:
        position_ids = [int(pid) for pid in json.loads(position_ids_json)]
    except (TypeError, ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, "项目参数格式错误") from e
    position_ids = list(dict.fromkeys(position_ids))
    if not position_ids:
        raise HTTPException(400, "请至少选择 1 个项目")
    if len(position_ids) > 10:
        raise HTTPException(400, "一次最多选择 10 个项目")
    conn = get_conn()
    placeholders = ",".join(["?"] * len(position_ids))
    projects = conn.execute(
        f"SELECT id FROM positions WHERE id IN ({placeholders}) AND status='active' AND is_project=1",
        position_ids,
    ).fetchall()
    valid_project_ids = [p["id"] for p in projects]
    if len(valid_project_ids) != len(position_ids):
        raise HTTPException(400, "所选项目不存在、已归档，或不是项目管理中的重点项目")
    if len(files) * len(valid_project_ids) > 100:
        raise HTTPException(400, "一次最多执行 10 份简历 × 10 个项目")

    user_id = _current_user_id(user) if _match_runs_has_owner(conn) else None
    uploaded, run_ids, warnings = [], [], []
    temp_data.cleanup_expired_temp_resume_files(conn)
    temp_data.cleanup_expired_temp_resume_payloads(conn)
    for file in files:
        try:
            item = await _save_temp_candidate(conn, file, user_id)
        except resume_parser.ScannedPDFError as e:
            warnings.append(f"{file.filename or '未命名文件'}：{e}")
            continue
        except Exception as e:
            warnings.append(f"{file.filename or '未命名文件'}：解析失败（{type(e).__name__}: {e}）")
            continue
        uploaded.append(item)
        if item["warnings"]:
            warnings.extend([f"{item['filename']}：{w}" for w in item["warnings"]])
        for position_id in valid_project_ids:
            run_id = _create_run_for_candidate(conn, item["candidate_id"], [position_id], user_id)
            run_ids.append(run_id)
    if not run_ids:
        conn.rollback()
        raise HTTPException(422, "没有简历解析成功：" + "；".join(warnings))
    conn.commit()
    if run_ids:
        match_queue.wake()
    return {
        "run_ids": run_ids,
        "reused_result_ids": [],
        "reused_results": [],
        "candidate_count": len(uploaded),
        "position_count": len(valid_project_ids),
        "match_count": len(run_ids),
        "new_match_count": len(run_ids),
        "reused_count": 0,
        "candidates": uploaded,
        "warnings": warnings,
    }


@router.get("/history-results")
def list_history_results(
    limit: int = 50,
    offset: int = 0,
    run_ids: str = "",
    result_ids: str = "",
    result_id: Optional[int] = None,
    position_id: Optional[int] = None,
    candidate_name: str = "",
    score_band: str = "",
    latest_only: bool = True,
    user=Depends(require_report_reader),
):
    limit, offset = _page(limit, offset)
    conn = get_conn()
    company_expr = company_display_expr(conn, "co")
    where = []
    params = []
    parsed_run_ids = _parse_ids_csv(run_ids)
    if parsed_run_ids:
        placeholders = ",".join(["?"] * len(parsed_run_ids))
        where.append(f"mr.run_id IN ({placeholders})")
        params.extend(parsed_run_ids)
    parsed_result_ids = _parse_ids_csv(result_ids)
    if parsed_result_ids:
        placeholders = ",".join(["?"] * len(parsed_result_ids))
        where.append(f"mr.id IN ({placeholders})")
        params.extend(parsed_result_ids)
    if result_id:
        where.append("mr.id=?")
        params.append(result_id)
    if position_id:
        where.append("mr.position_id=?")
        params.append(position_id)
    if candidate_name.strip():
        where.append("c.name LIKE ?")
        params.append(f"%{candidate_name.strip()}%")
    if score_band == "review":
        where.append("mr.final_score >= ?")
        params.append(settings.review_threshold)
    elif score_band == "no_review":
        where.append("(mr.final_score IS NULL OR mr.final_score < ?)")
        params.append(settings.review_threshold)
    if latest_only and not parsed_run_ids and not parsed_result_ids and not result_id:
        where.append(
            "mr.id IN ("
            "SELECT MAX(mr2.id)"
            " FROM match_results mr2"
            " JOIN match_runs r2 ON r2.id=mr2.run_id"
            " JOIN candidates c2 ON c2.id=r2.candidate_id"
            " GROUP BY c2.name, mr2.position_id"
            ")"
        )
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    total = conn.execute(
        "SELECT COUNT(*) AS c"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " JOIN candidates c ON c.id=r.candidate_id"
        " JOIN positions p ON p.id=mr.position_id"
        f"{where_sql}",
        params,
    ).fetchone()["c"]
    rows = conn.execute(
        "SELECT mr.id AS result_id, mr.run_id, mr.position_id, mr.final_score,"
        " mr.recommend_level, mr.exclude_reason, mr.review_status,"
        " r.status, r.total_tokens, r.created_at, r.finished_at, r.error,"
        " c.id AS candidate_id, c.name AS candidate_name, p.title AS position_title, p.seq_no,"
        f" {company_expr} AS company,"
        " (SELECT COUNT(*) FROM match_results mr2"
        "  JOIN match_runs r2 ON r2.id=mr2.run_id"
        "  JOIN candidates c2 ON c2.id=r2.candidate_id"
        "  WHERE c2.name=c.name AND mr2.position_id=mr.position_id"
        ") AS version_count"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " JOIN candidates c ON c.id=r.candidate_id"
        " JOIN positions p ON p.id=mr.position_id"
        " LEFT JOIN companies co ON co.id=p.company_id"
        f"{where_sql}"
        " ORDER BY mr.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.delete("/history-results/{result_id}")
def delete_history_result(result_id: int, user=Depends(require_match_operator)):
    conn = get_conn()
    _ensure_owned_result(conn, result_id, user)
    try:
        deleted = match_result_cleanup.delete_match_result(conn, result_id)
        conn.commit()
        return deleted
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        conn.rollback()
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise HTTPException(500, f"匹配报告删除失败：{exc}") from exc


@router.get("/history-results/{result_id}/versions")
def list_history_versions(result_id: int, user=Depends(require_report_reader)):
    conn = get_conn()
    company_expr = company_display_expr(conn, "co")
    _ensure_owned_result(conn, result_id, user)
    base = conn.execute(
        "SELECT c.name AS candidate_name, mr.position_id"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " JOIN candidates c ON c.id=r.candidate_id"
        " WHERE mr.id=?",
        (result_id,),
    ).fetchone()
    if not base:
        raise HTTPException(404, "匹配结果不存在")
    rows = conn.execute(
        "SELECT mr.id AS result_id, mr.run_id, mr.position_id, mr.final_score,"
        " mr.exclude_reason, mr.review_status,"
        " r.status, r.total_tokens, r.created_at, r.finished_at, r.error,"
        " c.name AS candidate_name, p.title AS position_title, p.seq_no,"
        f" {company_expr} AS company, 1 AS version_count"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " JOIN candidates c ON c.id=r.candidate_id"
        " JOIN positions p ON p.id=mr.position_id"
        " LEFT JOIN companies co ON co.id=p.company_id"
        " WHERE c.name=? AND mr.position_id=?"
        " ORDER BY mr.id DESC",
        (base["candidate_name"], base["position_id"]),
    ).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows), "limit": len(rows), "offset": 0}


@router.get("/history-options")
def list_history_options(user=Depends(require_report_reader)):
    conn = get_conn()
    company_expr = company_display_expr(conn, "co")
    position_rows = conn.execute(
        f"SELECT DISTINCT p.id, p.seq_no, p.title, {company_expr} AS company"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " JOIN positions p ON p.id=mr.position_id"
        " LEFT JOIN companies co ON co.id=p.company_id"
        " ORDER BY p.seq_no, p.title",
    ).fetchall()
    candidate_rows = conn.execute(
        "SELECT DISTINCT c.name"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " JOIN candidates c ON c.id=r.candidate_id"
        " WHERE c.name != ''"
        " ORDER BY c.name",
    ).fetchall()
    return {
        "positions": [dict(row) for row in position_rows],
        "candidates": [row["name"] for row in candidate_rows],
    }


def _load_done_match_context(
    conn,
    run_ids: list[int],
    result_id: Optional[int] = None,
    selected_result_ids: Optional[list[int]] = None,
    user=None,
    limit: int = 20,
) -> list[dict]:
    selected_result_ids = list(dict.fromkeys(int(item) for item in selected_result_ids or [] if item))
    if not run_ids and not result_id and not selected_result_ids:
        return []
    params = []
    where = []
    if selected_result_ids:
        placeholders = ",".join(["?"] * len(selected_result_ids))
        where.append(f"mr.id IN ({placeholders})")
        params.extend(selected_result_ids)
    elif run_ids:
        placeholders = ",".join(["?"] * len(run_ids))
        where.append(f"r.id IN ({placeholders})")
        params.extend(run_ids)
    if result_id and not selected_result_ids:
        where.append("mr.id=?")
        params.append(result_id)
    company_expr = company_display_expr(conn, "co")
    rows = conn.execute(
        "SELECT mr.id, mr.final_score, mr.recommend_level, mr.dims_json, mr.detail_rows_json,"
        " mr.suggestions_json, r.status, r.error, c.name AS candidate_name,"
        f" p.title AS position_title, {company_expr} AS company"
        " FROM match_runs r"
        " JOIN candidates c ON c.id=r.candidate_id"
        " LEFT JOIN match_results mr ON mr.run_id=r.id"
        " LEFT JOIN positions p ON p.id=mr.position_id"
        " LEFT JOIN companies co ON co.id=p.company_id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY mr.final_score DESC, mr.id DESC",
        params,
    ).fetchall()
    context = []
    for row in rows:
        if not row["id"] or row["final_score"] is None:
            continue
        dims = loads_or(row["dims_json"], {})
        detail_rows = loads_or(row["detail_rows_json"], [])
        suggestions = loads_or(row["suggestions_json"], [])
        hard = dims.get("hard") or {}
        priority = dims.get("priority") or {}
        context.append(
            {
                "报告ID": row["id"],
                "候选人": row["candidate_name"] or "",
                "项目": row["position_title"] or "",
                "客户": row["company"] or "",
                "综合参考得分": row["final_score"],
                "推荐等级": row["recommend_level"] or "",
                "硬性条件": {
                    "得分": hard.get("score"),
                    "满足": hard.get("satisfied"),
                    "不满足": hard.get("failed"),
                    "待确认": hard.get("pending"),
                },
                "优先条件": {
                    "得分": priority.get("score"),
                    "满足": priority.get("satisfied"),
                    "不满足": priority.get("failed"),
                    "待确认": priority.get("pending"),
                },
                "结论": dims.get("judgement") or "",
                "关键明细": [
                    {
                        "条件类型": item.get("condition_type"),
                        "条件": item.get("requirement"),
                        "判断": item.get("label"),
                        "依据": item.get("note"),
                    }
                    for item in detail_rows[:8]
                ],
                "建议": [item.get("text", "") for item in suggestions[:4] if isinstance(item, dict)],
            }
        )
        if len(context) >= limit:
            break
    return context


def _chat_scope_json(
    run_ids: list[int],
    result_id: Optional[int] = None,
    result_ids: Optional[list[int]] = None,
) -> str:
    normalized_run_ids = sorted(set(int(run_id) for run_id in run_ids if run_id))
    normalized_result_ids = sorted(set(int(item) for item in result_ids or [] if item))
    if result_id:
        return json.dumps(
            {"run_ids": normalized_run_ids, "result_id": int(result_id)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if normalized_result_ids:
        return json.dumps(
            {"run_ids": normalized_run_ids, "result_ids": normalized_result_ids},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return json.dumps(normalized_run_ids, separators=(",", ":"))


def _scope_run_ids(scope_json: str) -> list[int]:
    data = loads_or(scope_json or "[]", [])
    if isinstance(data, dict):
        return [int(run_id) for run_id in data.get("run_ids") or [] if run_id]
    if isinstance(data, list):
        return [int(run_id) for run_id in data if run_id]
    return []


def _scope_result_id(scope_json: str) -> Optional[int]:
    data = loads_or(scope_json or "[]", [])
    if isinstance(data, dict) and data.get("result_id"):
        return int(data["result_id"])
    return None


def _scope_result_ids(scope_json: str) -> list[int]:
    data = loads_or(scope_json or "[]", [])
    if isinstance(data, dict):
        return [int(item) for item in data.get("result_ids") or [] if item]
    return []


def _run_ids_for_result(conn, result_id: Optional[int]) -> list[int]:
    if not result_id:
        return []
    row = conn.execute(
        "SELECT run_id FROM match_results WHERE id=?",
        (result_id,),
    ).fetchone()
    return [row["run_id"]] if row else []


def _chat_title(
    conn,
    run_ids: list[int],
    result_id: Optional[int] = None,
    result_ids: Optional[list[int]] = None,
) -> str:
    if result_id:
        row = conn.execute(
            "SELECT c.name AS candidate_name, p.title AS position_title"
            " FROM match_results mr"
            " JOIN match_runs r ON r.id=mr.run_id"
            " JOIN candidates c ON c.id=r.candidate_id"
            " LEFT JOIN positions p ON p.id=mr.position_id"
            " WHERE mr.id=?",
            (result_id,),
        ).fetchone()
        if row:
            return f"{row['candidate_name'] or '候选人'}×{row['position_title'] or '项目'}"
    if result_ids:
        placeholders = ",".join(["?"] * len(result_ids))
        rows = conn.execute(
            "SELECT DISTINCT c.name AS candidate_name, p.title AS position_title"
            " FROM match_results mr"
            " JOIN match_runs r ON r.id=mr.run_id"
            " JOIN candidates c ON c.id=r.candidate_id"
            " LEFT JOIN positions p ON p.id=mr.position_id"
            f" WHERE mr.id IN ({placeholders})"
            " LIMIT 3",
            result_ids,
        ).fetchall()
        if rows:
            first = rows[0]
            suffix = "等" if len(rows) > 1 else ""
            return f"{first['candidate_name'] or '候选人'}×{first['position_title'] or '项目'}{suffix}"
    if not run_ids:
        return "匹配报告对话"
    placeholders = ",".join(["?"] * len(run_ids))
    rows = conn.execute(
        "SELECT DISTINCT c.name AS candidate_name, p.title AS position_title"
        " FROM match_runs r"
        " JOIN candidates c ON c.id=r.candidate_id"
        " LEFT JOIN match_results mr ON mr.run_id=r.id"
        " LEFT JOIN positions p ON p.id=mr.position_id"
        f" WHERE r.id IN ({placeholders})"
        " LIMIT 3",
        run_ids,
    ).fetchall()
    if not rows:
        return f"匹配报告对话 {','.join(map(str, run_ids[:3]))}"
    first = rows[0]
    suffix = "等" if len(rows) > 1 else ""
    return f"{first['candidate_name'] or '候选人'}×{first['position_title'] or '项目'}{suffix}"


def _get_or_create_chat_session(
    conn,
    run_ids: list[int],
    user: dict,
    result_id: Optional[int] = None,
    result_ids: Optional[list[int]] = None,
) -> int:
    ensure_chat_schema(conn)
    if result_id and not run_ids:
        run_ids = _run_ids_for_result(conn, result_id)
    run_ids_json = _chat_scope_json(run_ids, result_id, result_ids)
    row = conn.execute(
        "SELECT id FROM chat_sessions WHERE user_id=? AND run_ids_json=?",
        (user["id"], run_ids_json),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO chat_sessions (user_id, run_ids_json, title) VALUES (?,?,?)",
        (
            user["id"],
            run_ids_json,
            _chat_title(conn, run_ids, result_id, result_ids),
        ),
    )
    conn.commit()
    return cur.lastrowid


def _find_chat_session(
    conn,
    run_ids: list[int],
    user: dict,
    result_id: Optional[int] = None,
    result_ids: Optional[list[int]] = None,
) -> Optional[int]:
    ensure_chat_schema(conn)
    if result_id and not run_ids:
        run_ids = _run_ids_for_result(conn, result_id)
    run_ids_json = _chat_scope_json(run_ids, result_id, result_ids)
    row = conn.execute(
        "SELECT id FROM chat_sessions WHERE user_id=? AND run_ids_json=?",
        (user["id"], run_ids_json),
    ).fetchone()
    return int(row["id"]) if row else None


def _load_chat_history(conn, session_id: int, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def _estimate_tokens(*texts: str) -> int:
    total = sum(len(text or "") for text in texts)
    return max(1, round(total / 1.8))


def _chat_session_payload(conn, session_id: int, user: dict) -> dict:
    ensure_chat_schema(conn)
    can_view_all = user.get("role") in ("super_admin", "business_admin")
    owner_sql = "" if can_view_all else " AND s.user_id=?"
    owner_params = [] if can_view_all else [user["id"]]
    session = conn.execute(
        "SELECT s.*, u.display_name AS user_display_name"
        " FROM chat_sessions s"
        " LEFT JOIN users u ON u.id=s.user_id"
        f" WHERE s.id=?{owner_sql}",
        (session_id, *owner_params),
    ).fetchone()
    if not session:
        raise HTTPException(404, "对话会话不存在")
    messages = conn.execute(
        "SELECT id, role, content, model, tokens, created_at"
        " FROM chat_messages WHERE session_id=? ORDER BY id ASC LIMIT 200",
        (session_id,),
    ).fetchall()
    return {
        "session_id": session["id"],
        "title": session["title"] or "",
        "run_ids": _scope_run_ids(session["run_ids_json"] or "[]"),
        "result_id": _scope_result_id(session["run_ids_json"] or "[]"),
        "result_ids": _scope_result_ids(session["run_ids_json"] or "[]"),
        "user_display_name": session["user_display_name"] or "",
        "added_result_ids": loads_or(session["added_result_ids_json"] or "[]", []),
        "hidden_result_ids": loads_or(session["hidden_result_ids_json"] or "[]", []),
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "messages": [dict(row) for row in messages],
    }


def _save_chat_message(conn, session_id: int, role: str, content: str, model: str = "", tokens: int = 0) -> None:
    ensure_chat_schema(conn)
    cur = conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, model, tokens) VALUES (?,?,?,?,?)",
        (session_id, role, content, model, tokens),
    )
    conn.execute(
        "UPDATE chat_sessions SET updated_at=datetime('now','localtime') WHERE id=?",
        (session_id,),
    )
    conn.commit()
    feishu_store.push_chat_message(conn, cur.lastrowid)


def _save_auto_summary_once(
    conn,
    run_ids: list[int],
    user: dict,
    summary: str,
    model: str = "",
    tokens: int = 0,
    result_id: Optional[int] = None,
    result_ids: Optional[list[int]] = None,
) -> int:
    session_id = _get_or_create_chat_session(conn, run_ids, user, result_id, result_ids)
    existing = conn.execute(
        "SELECT id FROM chat_messages WHERE session_id=? AND role='assistant' AND content LIKE ? LIMIT 1",
        (session_id, f"{AUTO_SUMMARY_PREFIX}%"),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE chat_messages SET content=?, model=?, tokens=?, created_at=datetime('now','localtime') WHERE id=?",
            (f"{AUTO_SUMMARY_PREFIX}{summary}", model, tokens, existing["id"]),
        )
        conn.execute(
            "UPDATE chat_sessions SET updated_at=datetime('now','localtime') WHERE id=?",
            (session_id,),
        )
        conn.commit()
        return session_id
    try:
        _save_chat_message(conn, session_id, "assistant", f"{AUTO_SUMMARY_PREFIX}{summary}", model, tokens)
    except Exception:
        # 自动综述不能反过来阻断用户查看匹配结果；本地消息已在 _save_chat_message 内提交。
        pass
    return session_id


@router.get("/chat/sessions")
def list_chat_sessions(limit: int = 30, user=Depends(require_ai_operator)):
    limit = max(1, min(limit, 100))
    conn = get_conn()
    ensure_chat_schema(conn)
    can_view_all = user.get("role") in ("super_admin", "business_admin")
    where_sql = "" if can_view_all else " WHERE s.user_id=?"
    params = [] if can_view_all else [user["id"]]
    rows = conn.execute(
        "SELECT s.id, s.title, s.run_ids_json, s.created_at, s.updated_at,"
        " u.display_name AS user_display_name,"
        " COUNT(m.id) AS message_count,"
        " (SELECT m2.content FROM chat_messages m2 WHERE m2.session_id=s.id ORDER BY m2.id DESC LIMIT 1) AS last_message"
        " FROM chat_sessions s"
        " LEFT JOIN users u ON u.id=s.user_id"
        " LEFT JOIN chat_messages m ON m.session_id=s.id"
        f"{where_sql}"
        " GROUP BY s.id"
        " HAVING message_count > 0"
        " ORDER BY s.updated_at DESC, s.id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return {
        "items": [
            {
                "session_id": row["id"],
                "title": row["title"] or "",
                "run_ids": _scope_run_ids(row["run_ids_json"] or "[]"),
                "result_id": _scope_result_id(row["run_ids_json"] or "[]"),
                "result_ids": _scope_result_ids(row["run_ids_json"] or "[]"),
                "user_display_name": row["user_display_name"] or "",
                "message_count": row["message_count"],
                "last_message": row["last_message"] or "",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    }


@router.get("/chat/sessions/current")
def get_current_chat_session(
    run_ids: str = "",
    result_id: Optional[int] = None,
    result_ids: str = "",
    user=Depends(require_ai_operator),
):
    parsed_run_ids = _parse_ids_csv(run_ids)
    parsed_result_ids = _parse_ids_csv(result_ids)
    if len(parsed_run_ids) > 100:
        raise HTTPException(400, "一次最多基于 100 个 run 对话")
    conn = get_conn()
    parsed_result_ids = _owned_result_ids(conn, parsed_result_ids, user)
    session_id = _find_chat_session(conn, parsed_run_ids, user, result_id, parsed_result_ids)
    if not session_id:
        raise HTTPException(404, "对话会话不存在")
    return _chat_session_payload(conn, session_id, user)


@router.post("/chat/sessions/current")
def create_current_chat_session(body: MatchSummaryIn, user=Depends(require_ai_operator)):
    run_ids = list(dict.fromkeys(int(run_id) for run_id in body.run_ids if run_id))
    result_ids = list(dict.fromkeys(int(item) for item in body.result_ids if item))
    if len(run_ids) > 100:
        raise HTTPException(400, "一次最多基于 100 个 run 对话")
    result_id = int(body.result_id) if body.result_id else None
    conn = get_conn()
    result_ids = _owned_result_ids(conn, result_ids, user)
    session_id = _get_or_create_chat_session(conn, run_ids, user, result_id, result_ids)
    return _chat_session_payload(conn, session_id, user)


@router.get("/chat/workspace-state")
def get_chat_workspace_state(
    run_ids: str = "",
    result_id: Optional[int] = None,
    result_ids: str = "",
    user=Depends(require_ai_operator),
):
    parsed_run_ids = _parse_ids_csv(run_ids)
    parsed_result_ids = _parse_ids_csv(result_ids)
    if len(parsed_run_ids) > 100:
        raise HTTPException(400, "一次最多基于 100 个 run 对话")
    conn = get_conn()
    parsed_result_ids = _owned_result_ids(conn, parsed_result_ids, user)
    session_id = _find_chat_session(conn, parsed_run_ids, user, result_id, parsed_result_ids)
    if not session_id:
        return {"added_result_ids": [], "hidden_result_ids": []}
    session = conn.execute(
        "SELECT added_result_ids_json, hidden_result_ids_json FROM chat_sessions WHERE id=? AND user_id=?",
        (session_id, user["id"]),
    ).fetchone()
    return {
        "added_result_ids": loads_or(session["added_result_ids_json"] or "[]", []) if session else [],
        "hidden_result_ids": loads_or(session["hidden_result_ids_json"] or "[]", []) if session else [],
    }


@router.put("/chat/workspace-state")
def save_chat_workspace_state(body: MatchWorkspaceStateIn, user=Depends(require_ai_operator)):
    run_ids = list(dict.fromkeys(int(run_id) for run_id in body.run_ids if run_id))
    result_id = int(body.result_id) if body.result_id else None
    result_ids = list(dict.fromkeys(int(item) for item in body.result_ids if item))
    added_result_ids = list(dict.fromkeys(int(item) for item in body.added_result_ids if item))
    hidden_result_ids = list(dict.fromkeys(int(item) for item in body.hidden_result_ids if item))
    if len(run_ids) > 100:
        raise HTTPException(400, "一次最多基于 100 个 run 对话")
    if len(added_result_ids) > 500:
        raise HTTPException(400, "一个工作台最多添加 500 份报告")
    if len(hidden_result_ids) > 500:
        raise HTTPException(400, "一个工作台最多隐藏 500 份报告")
    conn = get_conn()
    result_ids = _owned_result_ids(conn, result_ids, user)
    added_result_ids = _owned_result_ids(conn, added_result_ids, user)
    session_id = _get_or_create_chat_session(conn, run_ids, user, result_id, result_ids)
    conn.execute(
        "UPDATE chat_sessions SET added_result_ids_json=?, hidden_result_ids_json=?,"
        " updated_at=datetime('now','localtime') WHERE id=?",
        (
            json.dumps(added_result_ids, separators=(",", ":")),
            json.dumps(hidden_result_ids, separators=(",", ":")),
            session_id,
        ),
    )
    conn.commit()
    return {"added_result_ids": added_result_ids, "hidden_result_ids": hidden_result_ids}


@router.get("/chat/sessions/{session_id}")
def get_chat_session(session_id: int, user=Depends(require_ai_operator)):
    conn = get_conn()
    return _chat_session_payload(conn, session_id, user)


@router.delete("/chat/sessions/{session_id}")
def delete_chat_session(session_id: int, user=Depends(require_ai_operator)):
    conn = get_conn()
    ensure_chat_schema(conn)
    can_manage_all = user.get("role") in ("super_admin", "business_admin")
    owner_sql = "" if can_manage_all else " AND user_id=?"
    owner_params = [] if can_manage_all else [user["id"]]
    session = conn.execute(
        f"SELECT id FROM chat_sessions WHERE id=?{owner_sql}",
        (session_id, *owner_params),
    ).fetchone()
    if not session:
        raise HTTPException(404, "对话会话不存在")
    message_rows = conn.execute(
        "SELECT feishu_record_id FROM chat_messages WHERE session_id=? AND feishu_record_id != ''",
        (session_id,),
    ).fetchall()
    for row in message_rows:
        try:
            feishu_store.delete_chat_message(row["feishu_record_id"])
        except Exception:
            pass
    conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
    conn.commit()
    return {"ok": True}



@router.post("/summary")
def generate_match_summary(body: MatchSummaryIn, user=Depends(require_ai_operator)):
    run_ids = list(dict.fromkeys(int(run_id) for run_id in body.run_ids if run_id))
    result_id = int(body.result_id) if body.result_id else None
    result_ids = list(dict.fromkeys(int(item) for item in body.result_ids if item))
    selected_result_ids = list(dict.fromkeys(int(item) for item in body.selected_result_ids if item))
    if len(selected_result_ids) > 30:
        raise HTTPException(400, "一次最多基于 30 份报告总结")
    if result_id and not run_ids:
        conn = get_conn()
        run_ids = _run_ids_for_result(conn, result_id)
    if not run_ids and not result_id and not result_ids and not selected_result_ids:
        return {"summary": "本次暂无可总结的匹配任务。", "model": "", "tokens": 0}
    if len(run_ids) > 100:
        raise HTTPException(400, "一次最多总结 100 个 run")
    conn = get_conn()
    result_ids = _owned_result_ids(conn, result_ids, user)
    if not run_ids and not result_id and not result_ids and not selected_result_ids:
        return {"summary": "本次暂无可总结的匹配任务。", "model": "", "tokens": 0}
    if result_ids and not run_ids and not result_id and not selected_result_ids:
        selected_result_ids = result_ids
    params = []
    where = []
    if selected_result_ids:
        placeholders = ",".join(["?"] * len(selected_result_ids))
        where.append(f"mr.id IN ({placeholders})")
        params.extend(selected_result_ids)
    elif run_ids:
        placeholders = ",".join(["?"] * len(run_ids))
        where.append(f"r.id IN ({placeholders})")
        params.extend(run_ids)
    if result_id and not selected_result_ids:
        where.append("mr.id=?")
        params.append(result_id)
    company_expr = company_display_expr(conn, "co")
    rows = conn.execute(
        "SELECT mr.id, mr.final_score, mr.recommend_level, mr.dims_json,"
        " r.status, r.error, c.name AS candidate_name, p.title AS position_title,"
        f" {company_expr} AS company"
        " FROM match_runs r"
        " JOIN candidates c ON c.id=r.candidate_id"
        " LEFT JOIN match_results mr ON mr.run_id=r.id"
        " LEFT JOIN positions p ON p.id=mr.position_id"
        " LEFT JOIN companies co ON co.id=p.company_id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY mr.final_score DESC, mr.id DESC",
        params,
    ).fetchall()
    done_rows = [dict(row) for row in rows if row["id"] and row["final_score"] is not None]
    if not done_rows:
        failed = sum(1 for row in rows if row["status"] == "failed")
        running = max(0, len(run_ids) - failed)
        return {
            "summary": f"本次匹配仍在整理中，当前还有 {running} 个任务未完成，{failed} 个任务失败。完成后我会按人才和项目生成匹配报告。",
            "model": "",
            "tokens": 0,
            "session_id": _get_or_create_chat_session(conn, run_ids, user, result_id, result_ids),
        }

    candidates = {row["candidate_name"] for row in done_rows if row.get("candidate_name")}
    positions = {row["position_title"] for row in done_rows if row.get("position_title")}
    review_count = sum(1 for row in done_rows if row.get("final_score") is not None and row["final_score"] >= settings.review_threshold)
    samples = []
    for row in done_rows[:12]:
        dims = loads_or(row.get("dims_json"), {})
        hard = dims.get("hard") or {}
        priority = dims.get("priority") or {}
        samples.append(
            {
                "候选人": row.get("candidate_name") or "",
                "项目": row.get("position_title") or "",
                "客户": row.get("company") or "",
                "综合参考得分": row.get("final_score"),
                "硬性条件得分": hard.get("score"),
                "硬性不满足": hard.get("failed"),
                "硬性待确认": hard.get("pending"),
                "优先条件得分": priority.get("score"),
                "优先待确认": priority.get("pending"),
                "结论": dims.get("judgement") or row.get("recommend_level") or "",
            }
        )
    system = (
        "你是猎头匹配助手。请根据本次人才×项目匹配结果，生成给顾问看的中文综述。"
        "要求：不超过180字；先说明本次完成了多少人才、多少项目、多少份报告；"
        "点名最值得优先查看的1-2个匹配；指出硬性条件不满足或待确认风险；"
        "不要编造未给出的经历、公司信息或分数。只输出综述正文。"
    )
    summary_user_prompt = json.dumps(
        {
            "人才数": len(candidates),
            "项目数": len(positions),
            "报告数": len(done_rows),
            "进入人工审核阈值": settings.review_threshold,
            "进入人工审核报告数": review_count,
            "报告样本": samples,
        },
        ensure_ascii=False,
    )
    try:
        ai_usage.check_daily_quota(conn, user)
        result = llm.chat(system, summary_user_prompt, temperature=0.2)
        text = result.text.strip().strip('"')
        session_id = _save_auto_summary_once(conn, run_ids, user, text, settings.openai_model, result.tokens, result_id, result_ids)
        ai_usage.record_usage(
            conn,
            user,
            "match.summary",
            settings.openai_model,
            result.tokens,
            {"run_ids": run_ids, "result_id": result_id, "result_ids": result_ids, "selected_result_ids": selected_result_ids},
        )
        return {"summary": text, "model": settings.openai_model, "tokens": result.tokens, "session_id": session_id}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        best = done_rows[0]
        text = (
            f"本次已完成 {len(candidates)} 位人才 × {len(positions)} 个项目的匹配，"
            f"共生成 {len(done_rows)} 份报告。当前得分最高的是「{best['candidate_name']}」"
            f"匹配「{best['position_title']}」，综合参考得分 {best['final_score']} 分；"
            f"共有 {review_count} 份报告达到人工审核阈值。"
        )
        session_id = _save_auto_summary_once(conn, run_ids, user, text, result_id=result_id, result_ids=result_ids)
        return {
            "summary": text,
            "model": settings.openai_model,
            "tokens": 0,
            "error": str(exc),
            "session_id": session_id,
        }


@router.post("/chat")
def chat_about_match(body: MatchChatIn, user=Depends(require_ai_operator)):
    run_ids = list(dict.fromkeys(int(run_id) for run_id in body.run_ids if run_id))
    result_id = int(body.result_id) if body.result_id else None
    result_ids = list(dict.fromkeys(int(item) for item in body.result_ids if item))
    selected_result_ids = list(dict.fromkeys(int(item) for item in body.selected_result_ids if item))
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "请输入要咨询的问题")
    if len(run_ids) > 100:
        raise HTTPException(400, "一次最多基于 100 个 run 对话")
    if len(selected_result_ids) > 30:
        raise HTTPException(400, "一次最多基于 30 份报告对话")
    conn = get_conn()
    result_ids = _owned_result_ids(conn, result_ids, user)
    context = _load_done_match_context(conn, run_ids, result_id, selected_result_ids, user)
    if context:
        ai_usage.check_daily_quota(conn, user)
    session_id = _get_or_create_chat_session(conn, run_ids, user, result_id, result_ids)
    _save_chat_message(conn, session_id, "user", message)
    if not context:
        reply = "当前还没有可用的匹配报告，我需要等报告生成完成后，才能基于具体得分、条件明细和风险点回答。"
        _save_chat_message(conn, session_id, "assistant", reply)
        return {
            "reply": reply,
            "model": "",
            "tokens": 0,
        }
    history = _load_chat_history(conn, session_id, 16)[:-1]
    system = (
        "你是猎头匹配报告解释助手，只能基于给定的匹配报告上下文回答。"
        "你可以帮助顾问比较候选人和项目、解释得分、指出硬性风险/待确认项、建议下一步沟通问题。"
        "如果用户要求修改匹配结论或新增客户要求，要说明当前回答不会改写已生成报告；如需重新计分，需要回到发起匹配重新跑。"
        "不要编造简历、JD、客户背景或报告中没有的信息。回答要简洁、专业、可执行。"
    )
    user_prompt = json.dumps(
        {
            "本次匹配报告上下文": context,
            "最近对话": history,
            "用户问题": message,
        },
        ensure_ascii=False,
    )
    result = llm.chat(system, user_prompt, temperature=0.2)
    reply = result.text.strip()
    _save_chat_message(conn, session_id, "assistant", reply, settings.openai_model, result.tokens)
    ai_usage.record_usage(
        conn,
        user,
        "match.chat",
        settings.openai_model,
        result.tokens,
        {"run_ids": run_ids, "result_id": result_id, "result_ids": result_ids, "selected_result_ids": selected_result_ids},
    )
    return {"reply": reply, "model": settings.openai_model, "tokens": result.tokens, "session_id": session_id}


def _sse(data: dict) -> str:
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


@router.post("/chat/stream")
def stream_chat_about_match(body: MatchChatIn, user=Depends(require_ai_operator)):
    run_ids = list(dict.fromkeys(int(run_id) for run_id in body.run_ids if run_id))
    result_id = int(body.result_id) if body.result_id else None
    result_ids = list(dict.fromkeys(int(item) for item in body.result_ids if item))
    selected_result_ids = list(dict.fromkeys(int(item) for item in body.selected_result_ids if item))
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "请输入要咨询的问题")
    if len(run_ids) > 100:
        raise HTTPException(400, "一次最多基于 100 个 run 对话")
    if len(selected_result_ids) > 30:
        raise HTTPException(400, "一次最多基于 30 份报告对话")
    conn = get_conn()
    result_ids = _owned_result_ids(conn, result_ids, user)
    context = _load_done_match_context(conn, run_ids, result_id, selected_result_ids, user)
    if context:
        ai_usage.check_daily_quota(conn, user)
    session_id = _get_or_create_chat_session(conn, run_ids, user, result_id, result_ids)
    _save_chat_message(conn, session_id, "user", message)
    history = _load_chat_history(conn, session_id, 16)[:-1]

    def generate():
        if not context:
            reply = "当前还没有可用的匹配报告，我需要等报告生成完成后，才能基于具体得分、条件明细和风险点回答。"
            _save_chat_message(conn, session_id, "assistant", reply)
            yield _sse({
                "type": "delta",
                "text": reply,
            })
            yield _sse({"type": "done", "model": "", "tokens": 0, "session_id": session_id})
            return
        system = (
            "你是猎头匹配报告解释助手，只能基于给定的匹配报告上下文回答。"
            "你可以帮助顾问比较候选人和项目、解释得分、指出硬性风险/待确认项、建议下一步沟通问题。"
            "如果用户要求修改匹配结论或新增客户要求，要说明当前回答不会改写已生成报告；如需重新计分，需要回到发起匹配重新跑。"
            "不要编造简历、JD、客户背景或报告中没有的信息。回答要简洁、专业、可执行。"
        )
        user_prompt = json.dumps(
            {
                "本次匹配报告上下文": context,
                "最近对话": history,
                "用户问题": message,
            },
            ensure_ascii=False,
        )
        try:
            full_text = ""
            for text in llm.chat_stream(system, user_prompt, temperature=0.2):
                full_text += text
                yield _sse({"type": "delta", "text": text})
            tokens = _estimate_tokens(system, user_prompt, full_text)
            _save_chat_message(conn, session_id, "assistant", full_text.strip(), settings.openai_model, tokens)
            ai_usage.record_usage(
                conn,
                user,
                "match.chat.stream",
                settings.openai_model,
                tokens,
                {"run_ids": run_ids, "result_id": result_id, "result_ids": result_ids, "selected_result_ids": selected_result_ids},
            )
            yield _sse({"type": "done", "model": settings.openai_model, "tokens": tokens, "session_id": session_id})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}")
def get_run(run_id: int, user=Depends(require_report_reader)):
    conn = get_conn()
    run = conn.execute("SELECT * FROM match_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise HTTPException(404, "run 不存在")
    return {
        "run_id": run["id"],
        "status": run["status"],
        "error": run["error"],
        "total_tokens": run["total_tokens"],
        "progress": matcher.run_progress(conn, run),
    }


@router.post("/runs/status")
def get_runs_status(body: BatchStatusIn, user=Depends(require_report_reader)):
    run_ids = list(dict.fromkeys(int(run_id) for run_id in body.run_ids if run_id))
    if not run_ids:
        return {"items": [], "done": 0, "partial_failed": 0, "failed": 0, "running": 0, "total": 0}
    if len(run_ids) > 100:
        raise HTTPException(400, "一次最多查询 100 个 run")
    conn = get_conn()
    placeholders = ",".join(["?"] * len(run_ids))
    rows = conn.execute(
        f"SELECT * FROM match_runs WHERE id IN ({placeholders})",
        run_ids,
    ).fetchall()
    found = {row["id"]: row for row in rows}
    items = []
    for run_id in run_ids:
        row = found.get(run_id)
        if not row:
            items.append({
                "run_id": run_id,
                "status": "failed",
                "error": "run 不存在或无权限查看",
                "total_tokens": 0,
                "progress": {
                    "total_positions": 0,
                    "results_saved": 0,
                    "valid_results": 0,
                    "failed_results": 0,
                    "status": "failed",
                },
            })
            continue
        items.append({
            "run_id": row["id"],
            "status": row["status"],
            "error": row["error"],
            "total_tokens": row["total_tokens"],
            "progress": matcher.run_progress(conn, row),
        })
    done_count = sum(
        1
        for item in items
        if item["status"] in SUCCESS_RUN_STATUSES and item["progress"].get("valid_results", 0) > 0
    )
    failed_count = sum(
        1
        for item in items
        if item["status"] == "failed"
        or (item["status"] in SUCCESS_RUN_STATUSES and item["progress"].get("valid_results", 0) == 0)
    )
    return {
        "items": items,
        "done": done_count,
        "partial_failed": sum(
            1
            for item in items
            if item["status"] == "done_with_errors" and item["progress"].get("valid_results", 0) > 0
        ),
        "failed": failed_count,
        "running": sum(1 for item in items if item["status"] not in TERMINAL_RUN_STATUSES),
        "total": len(items),
    }


def _report_for_result(conn, result_id: int, user=None) -> dict:
    row = conn.execute(
        "SELECT mr.run_id, mr.position_id, mr.final_score, r.status"
        " FROM match_results mr JOIN match_runs r ON r.id=mr.run_id"
        " WHERE mr.id=?",
        (result_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "匹配结果不存在")
    if row["final_score"] is None:
        if row["status"] in TERMINAL_RUN_STATUSES:
            raise HTTPException(409, "该岗位明细分析失败，没有可查看的有效报告")
        raise HTTPException(409, f"run 尚未完成（当前状态：{row['status']}）")
    run = conn.execute("SELECT * FROM match_runs WHERE id=?", (row["run_id"],)).fetchone()
    data = report.build_report(conn, run)
    position_id = row["position_id"]
    data["details"] = [item for item in data["details"] if item["position_id"] == position_id]
    return data


@router.get("/results/{result_id}/report")
def get_result_report(result_id: int, user=Depends(require_report_reader)):
    conn = get_conn()
    _ensure_owned_result(conn, result_id, user)
    return _report_for_result(conn, result_id)


def _generate_client_recommendation(conn, result_id: int, actor: dict, force: bool = False) -> dict:
    _ensure_owned_result(conn, result_id, actor)
    if not force:
        existing = conn.execute(
            "SELECT * FROM client_recommendation_reports WHERE result_id=? ORDER BY version DESC, id DESC LIMIT 1",
            (result_id,),
        ).fetchone()
        if existing:
            return _client_recommendation_from_row(existing)
    data = _report_for_result(conn, result_id)
    detail = (data.get("details") or [None])[0]
    if not detail:
        raise HTTPException(409, "当前匹配结果没有可生成客户推荐报告的明细")
    candidate_row = conn.execute(
        "SELECT resume_text, resume_struct_json FROM candidates WHERE id=?",
        (data["candidate_id"],),
    ).fetchone()
    candidate = data.get("candidate") or {}
    resume_text = (candidate_row["resume_text"] if candidate_row else "") or ""
    context = {
        "候选人": {
            "姓名": data.get("candidate_name") or "",
            "年龄": candidate.get("age") or "",
            "城市": candidate.get("city") or "",
            "目前状态": candidate.get("talent_status") or "",
            "目前薪酬": candidate.get("current_salary") or "",
            "期望薪酬": candidate.get("expected_salary") or "",
            "当前公司": candidate.get("current_company") or "",
            "当前职位": candidate.get("current_title") or "",
            "标签": candidate.get("tags") or "",
        },
        "推荐岗位": {
            "岗位": detail.get("title") or "",
            "客户": detail.get("company") or "",
            "综合参考得分": detail.get("final_score"),
            "硬性条件得分": (detail.get("score_summary") or {}).get("hard", {}).get("score"),
            "优先条件得分": (detail.get("score_summary") or {}).get("priority", {}).get("score"),
        },
        "匹配明细": [
            {
                "条件类型": row.get("condition_type"),
                "条件": row.get("requirement"),
                "判断": row.get("label"),
                "依据": row.get("note"),
            }
            for row in (detail.get("rows") or [])[:16]
        ],
        "内部建议": [item.get("text", "") for item in (detail.get("suggestions") or [])[:5]],
        "简历摘要原文": resume_text[:6000],
    }
    system = (
        "你是资深猎头顾问，要把内部匹配报告改写成可发给客户看的候选人推荐报告。"
        "只基于输入内容写，不得编造学历、薪资、公司、项目、数据、离职原因。"
        "客户版不要出现内部评分、人工审核、模型、硬性/优先条件表、待确认计分规则等内部措辞。"
        "推荐理由要像猎头真实推荐语：聚焦岗位相关能力、业务成果、平台/行业背景、可验证亮点。"
        "如果某字段缺失，填“待确认”。输出 JSON，格式："
        '{"fields":{"推荐人选":"","推荐岗位":"","性别":"","年龄":"","学历":"","期望地点":"","目前状态":"","目前薪酬":"","期望薪酬":"","用工形式":""},'
        '"reasons":[{"title":"","text":""}],"leave_reason":"","ps":""}'
        "reasons 生成 3-4 条，每条 title 不超过 18 字，text 不超过 90 字。"
    )
    user = json.dumps(context, ensure_ascii=False)
    tokens = 0
    try:
        ai_usage.check_daily_quota(conn, actor)
        parsed, _raw, tokens, _latency = llm.chat_json(system, user, temperature=0.2)
        if not isinstance(parsed, dict):
            raise ValueError("AI 输出不是 JSON 对象")
        fields = parsed.get("fields") if isinstance(parsed.get("fields"), dict) else {}
        reasons = parsed.get("reasons") if isinstance(parsed.get("reasons"), list) else []
        cleaned_reasons = []
        for item in reasons:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            text = str(item.get("text") or "").strip()
            if title or text:
                cleaned_reasons.append({"title": title or "推荐理由", "text": text})
        fallback = _fallback_client_recommendation(data)
        payload = {
            "fields": {**fallback["fields"], **{k: str(v) for k, v in fields.items()}},
            "reasons": cleaned_reasons[:4] or fallback["reasons"],
            "leave_reason": str(parsed.get("leave_reason") or "待顾问确认补充"),
            "ps": str(parsed.get("ps") or fallback["ps"]),
        }
        saved = _save_client_recommendation(conn, result_id, payload, settings.openai_model, tokens)
        ai_usage.record_usage(
            conn,
            actor,
            "match.client_recommendation",
            settings.openai_model,
            tokens,
            {"result_id": result_id, "force": force},
        )
        return {**saved, "is_ai": True}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if tokens:
            ai_usage.record_usage(
                conn,
                actor,
                "match.client_recommendation",
                settings.openai_model,
                tokens,
                {"result_id": result_id, "force": force, "failed": True},
            )
        fallback = _fallback_client_recommendation(data)
        payload = {
            "fields": fallback["fields"],
            "reasons": fallback["reasons"],
            "leave_reason": fallback["leave_reason"],
            "ps": fallback["ps"],
        }
        saved = _save_client_recommendation(conn, result_id, payload, "", 0)
        return {**saved, "is_ai": False, "model": settings.openai_model, "error": str(exc)}


@router.get("/results/{result_id}/client-recommendation")
def get_client_recommendation(result_id: int, user=Depends(require_report_reader)):
    conn = get_conn()
    _ensure_owned_result(conn, result_id, user)
    _report_for_result(conn, result_id)
    existing = conn.execute(
        "SELECT * FROM client_recommendation_reports WHERE result_id=? ORDER BY version DESC, id DESC LIMIT 1",
        (result_id,),
    ).fetchone()
    if not existing:
        raise HTTPException(404, "客户推荐报告尚未生成")
    return _client_recommendation_from_row(existing)


@router.post("/results/{result_id}/client-recommendation/generate")
def generate_client_recommendation(result_id: int, user=Depends(require_client_report_operator)):
    conn = get_conn()
    return _generate_client_recommendation(conn, result_id, user, force=False)


@router.post("/results/{result_id}/client-recommendation/regenerate")
def regenerate_client_recommendation(result_id: int, user=Depends(require_client_report_operator)):
    conn = get_conn()
    return _generate_client_recommendation(conn, result_id, user, force=True)


@router.put("/results/{result_id}/client-recommendation")
def save_client_recommendation(result_id: int, body: ClientRecommendationIn, _user=Depends(require_client_report_operator)):
    conn = get_conn()
    _ensure_owned_result(conn, result_id, _user)
    _report_for_result(conn, result_id)
    payload = {
        "fields": body.fields or {},
        "reasons": body.reasons or [],
        "leave_reason": body.leave_reason or "",
        "ps": body.ps or "",
    }
    return _save_client_recommendation(conn, result_id, payload, "manual", 0, body.status or "草稿")


@router.get("/results/{result_id}/export.docx")
def export_result_docx(result_id: int, user=Depends(require_report_reader)):
    conn = get_conn()
    _ensure_owned_result(conn, result_id, user)
    data = _report_for_result(conn, result_id)
    title = data["candidate_name"]
    if data.get("details"):
        title += "_" + data["details"][0].get("title", "")
    path = report_export.build_docx(data)
    return _export_file_response(path, report_export.safe_filename(title + "_匹配报告", ".docx"))


@router.get("/results/{result_id}/export.pdf")
def export_result_pdf(result_id: int, user=Depends(require_report_reader)):
    conn = get_conn()
    _ensure_owned_result(conn, result_id, user)
    data = _report_for_result(conn, result_id)
    title = data["candidate_name"]
    if data.get("details"):
        title += "_" + data["details"][0].get("title", "")
    path = report_export.build_pdf(data)
    return _export_file_response(path, report_export.safe_filename(title + "_匹配报告", ".pdf"))
