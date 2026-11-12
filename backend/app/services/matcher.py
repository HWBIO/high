"""匹配流水线：已选项目逐项明细（并发）→ 结果落库。

由数据库持久队列 worker 执行；各线程用自己的 SQLite 连接。"""
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import settings
from ..db import company_display_expr, get_conn, loads_or
from . import feishu_store, jd_extractor, job_label_dictionary, llm, prompt_composer, scoring

ALLOWED_SUGGESTION_TYPES = {"resume_add", "resume_emphasize", "clarify_with_candidate"}
MAINLINE_REQUIREMENT = "岗位主线一致性"
EXPLICIT_BLOCKER_TERMS = ("一票否决", "淘汰", "硬性", "强制", "必须", "不可接受", "不接受", "不考虑")
SUCCESS_RUN_STATUSES = ("done", "done_with_errors")
TERMINAL_RUN_STATUSES = (*SUCCESS_RUN_STATUSES, "failed")


def _set_status(run_id: int, status: str, error: str = ""):
    conn = get_conn()
    if status in TERMINAL_RUN_STATUSES:
        conn.execute(
            "UPDATE match_runs SET status=?, error=?, finished_at=datetime('now','localtime') WHERE id=?",
            (status, error, run_id),
        )
    else:
        conn.execute("UPDATE match_runs SET status=? WHERE id=?", (status, run_id))
    conn.commit()


def _add_tokens(run_id: int, tokens: int):
    conn = get_conn()
    conn.execute(
        "UPDATE match_runs SET total_tokens = total_tokens + ? WHERE id=?",
        (tokens, run_id),
    )
    conn.commit()


def _load_positions(conn, position_ids: list) -> list:
    """加载岗位并保证结构化数据就绪，附上 company_name / jd_struct。"""
    positions = []
    company_expr = company_display_expr(conn, "c")
    for pid in position_ids:
        row = conn.execute(
            f"SELECT p.*, {company_expr} AS company_name FROM positions p "
            "LEFT JOIN companies c ON c.id = p.company_id WHERE p.id=?",
            (pid,),
        ).fetchone()
        if not row:
            continue
        pos = dict(row)
        pos["jd_struct"] = jd_extractor.ensure_struct(conn, row)
        positions.append(pos)
    return positions


def _normalize_detail_rows(raw_rows: list) -> list:
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        requirement = str(raw.get("requirement") or "").strip()
        if not requirement:
            continue
        label = scoring.normalize_label(raw.get("label"))
        condition_type = scoring.normalize_condition_type(raw.get("condition_type"))
        note = str(raw.get("note") or "").strip()
        rows.append(
            {
                "requirement": requirement,
                "condition_type": condition_type,
                "label": label,
                "note": note,
            }
        )
    return rows


def _has_explicit_blocker_language(text: str) -> bool:
    return any(term in text for term in EXPLICIT_BLOCKER_TERMS)


def _looks_like_age_inferred_from_education(requirement: str, note: str) -> bool:
    text = requirement + "\n" + note
    if "年龄" not in text and "岁" not in text:
        return False
    return any(term in note for term in ("入学", "毕业", "教育经历", "教育时间", "推测", "推断", "估算", "未明确", "未写", "需顾问确认"))


def _looks_like_stability_requirement(requirement: str, note: str) -> bool:
    text = requirement + "\n" + note
    return any(term in text for term in ("跳槽", "稳定性", "稳定度", "任职时长", "服务年限", "每家公司服务年限"))


def _looks_like_java_extra_preference(requirement: str, note: str, position: dict) -> bool:
    position_text = "\n".join(
        str(position.get(key) or "")
        for key in ("title", "name", "job_title", "jd", "jd_text", "description")
    )
    if not re.search(r"\bjava\b|Java|JAVA|高级Java|Java研发", position_text):
        return False
    text = requirement + "\n" + note
    return any(term in text for term in ("Node", "node", "Python", "python", "AI Agent", "大模型", "LLM", "Agent"))


def _ensure_mainline_row(rows: list, analysis: str) -> list:
    if any(MAINLINE_REQUIREMENT in str(row.get("requirement") or "") for row in rows):
        return rows

    analysis_text = str(analysis or "")
    if any(term in analysis_text for term in ("主线明显不一致", "岗位主线明显不一致", "核心能力错位", "主线经历明显不同")):
        label = "不满足"
        condition_type = "blocker"
        note = "analysis 已判断岗位主线明显不一致，系统补齐固定判断行并触发主线封顶。"
    elif any(term in analysis_text for term in ("相邻岗位", "岗位族相邻", "主线相近", "不完全一致")):
        label = "待确认"
        condition_type = "core"
        note = "analysis 显示岗位主线相邻但不完全一致，系统补齐固定判断行并触发相邻岗位族封顶。"
    else:
        label = "满足"
        condition_type = "core"
        note = "analysis 未提示岗位主线不一致，系统补齐固定判断行。"

    return [{"requirement": MAINLINE_REQUIREMENT, "condition_type": condition_type, "label": label, "note": note}, *rows]


def _postprocess_detail_rows(rows: list, analysis: str, position: dict) -> list:
    processed = []
    for row in rows:
        requirement = row["requirement"]
        note = row["note"]
        combined = requirement + "\n" + note
        normalized = dict(row)

        if normalized["condition_type"] == "blocker" and _looks_like_age_inferred_from_education(requirement, note):
            normalized["condition_type"] = "risk"
            normalized["label"] = "待确认"
            normalized["note"] = "简历未明确年龄或出生年份，不能从入学/毕业年份反推年龄；如客户有明确年龄限制，需顾问单独确认。"
        elif normalized["condition_type"] == "blocker" and _looks_like_stability_requirement(requirement, note) and not _has_explicit_blocker_language(combined):
            normalized["condition_type"] = "risk"
        elif normalized["condition_type"] in ("blocker", "core") and _looks_like_java_extra_preference(requirement, note, position) and not _has_explicit_blocker_language(combined):
            normalized["condition_type"] = "preference"

        processed.append(normalized)

    return _ensure_mainline_row(processed, analysis)


def _score_from_rows(rows: list) -> dict:
    return scoring.compose_condition_scores(
        rows,
        review_threshold=settings.review_threshold,
        top_threshold=settings.top_threshold,
    )


def _active_prompt_record(conn, layer: str, company_id=None, position_id=None, user_id=None) -> dict:
    if layer == "general":
        if user_id:
            pref = conn.execute(
                "SELECT general_prompt_id FROM user_prompt_preferences WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if pref and pref["general_prompt_id"]:
                row = conn.execute(
                    "SELECT id, content, version, scope, owner_user_id FROM prompts"
                    " WHERE id=? AND layer='general'",
                    (pref["general_prompt_id"],),
                ).fetchone()
                if row:
                    return dict(row)
        row = conn.execute(
            "SELECT id, content, version, scope, owner_user_id FROM prompts"
            " WHERE layer='general' AND scope='company_default' "
            "ORDER BY is_active DESC, version DESC LIMIT 1"
        ).fetchone()
    elif layer == "company":
        if not company_id:
            return {"id": None, "version": None, "content": ""}
        row = conn.execute(
            "SELECT id, content, version FROM prompts WHERE layer='company' AND company_id=? "
            "ORDER BY is_active DESC, version DESC LIMIT 1",
            (company_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, content, version FROM prompts WHERE layer='position' AND position_id=? "
            "ORDER BY is_active DESC, version DESC LIMIT 1",
            (position_id,),
        ).fetchone()
    return dict(row) if row else {"id": None, "version": None, "content": ""}


def _input_signature(candidate: dict, position: dict, general: dict, company: dict, position_prompt: dict, label_context: str = "") -> str:
    payload = {
        "candidate": {
            "name": candidate.get("name"),
            "resume_text_hash": hashlib.sha256((candidate.get("resume_text") or "").encode("utf-8")).hexdigest(),
            "resume_struct_hash": hashlib.sha256((candidate.get("resume_struct_json") or "").encode("utf-8")).hexdigest(),
            "city": candidate.get("city") or "",
            "salary": [candidate.get("current_salary") or "", candidate.get("expected_salary") or ""],
            "tags": candidate.get("tags") or "",
        },
        "position": {
            "id": position.get("id"),
            "jd_hash": position.get("jd_hash") or "",
            "jd_struct_hash": hashlib.sha256(json.dumps(position.get("jd_struct") or {}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "hard_struct_hash": hashlib.sha256((position.get("hard_struct_json") or "").encode("utf-8")).hexdigest(),
            "consultant_notes": position.get("consultant_notes") or "",
            "hard_requirements": position.get("hard_requirements") or "",
            "nice_to_have": position.get("nice_to_have") or "",
        },
        "prompts": {
            "general": [general.get("id"), general.get("version"), hashlib.sha256((general.get("content") or "").encode("utf-8")).hexdigest()],
            "company": [company.get("id"), company.get("version"), hashlib.sha256((company.get("content") or "").encode("utf-8")).hexdigest()],
            "position": [position_prompt.get("id"), position_prompt.get("version"), hashlib.sha256((position_prompt.get("content") or "").encode("utf-8")).hexdigest()],
            "job_label_context_hash": hashlib.sha256((label_context or "").encode("utf-8")).hexdigest(),
            "fixed_detail_stage": hashlib.sha256(prompt_composer.compose_system("", "detail").encode("utf-8")).hexdigest(),
            "scoring_policy": scoring.SCORING_POLICY_VERSION,
        },
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _find_reusable_result(conn, position_id: int, signature: str):
    if not signature:
        return None
    return conn.execute(
        "SELECT mr.* FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " WHERE mr.position_id=? AND mr.input_signature=? AND mr.final_score IS NOT NULL"
        " AND r.status IN ('done','done_with_errors')"
        " ORDER BY mr.id DESC LIMIT 1",
        (position_id, signature),
    ).fetchone()


def _reuse_detail(row) -> dict:
    return {
        "position_id": row["position_id"],
        "final_score": row["final_score"],
        "recommend_level": row["recommend_level"],
        "rows": json.loads(row["detail_rows_json"] or "[]"),
        "dims": json.loads(row["dims_json"] or "{}"),
        "suggestions": json.loads(row["suggestions_json"] or "[]"),
        "analysis": "输入未变化，已复用历史匹配报告。",
        "raw": row["raw_llm_output"] or "",
        "tokens": 0,
        "latency_ms": 0,
        "reused_from_result_id": row["id"],
    }


def _detail_one(position: dict, resume_text: str, system: str, run_id: int, candidate: dict = None, user_id=None) -> dict:
    conn = get_conn()
    general_prompt = _active_prompt_record(conn, "general", user_id=user_id)
    company_prompt = _active_prompt_record(conn, "company", company_id=position.get("company_id"))
    position_prompt = _active_prompt_record(conn, "position", position_id=position["id"])
    label_context = job_label_dictionary.compose_label_block(
        position,
        company_prompt["content"],
        position_prompt["content"],
    )
    signature = _input_signature(candidate or {}, position, general_prompt, company_prompt, position_prompt, label_context)
    reusable = _find_reusable_result(conn, position["id"], signature)
    if reusable:
        detail = _reuse_detail(reusable)
        detail["input_signature"] = signature
        detail["user_prompt"] = "输入签名未变化，复用历史结果，未重新请求 LLM。"
        return detail
    user = prompt_composer.compose_detail_user(
        position,
        resume_text,
        company_prompt["content"],
        position_prompt["content"],
        candidate,
        label_context=label_context,
    )
    start = time.time()
    parsed, raw, tokens, latency = llm.chat_json(system, user)
    _add_tokens(run_id, tokens)
    # 模型偶发把对象包成数组（如 [ {...} ]），取第一个字典元素纠正
    if isinstance(parsed, list):
        parsed = next((x for x in parsed if isinstance(x, dict)), {})
    if not isinstance(parsed, dict):
        raise ValueError(f"明细输出不是 JSON 对象: {str(parsed)[:80]}")
    if not parsed.get("rows"):
        raise ValueError("明细输出缺少 rows 字段（疑似输出被截断）")

    analysis = parsed.get("analysis") or ""
    rows = _postprocess_detail_rows(_normalize_detail_rows(parsed.get("rows") or []), analysis, position)
    if not rows:
        raise ValueError("明细输出 rows 为空或结构非法")
    suggestions = []
    for s in parsed.get("suggestions") or []:
        stype = s.get("type") if s.get("type") in ALLOWED_SUGGESTION_TYPES else "clarify_with_candidate"
        text = (s.get("text") or "").strip()
        if text:
            suggestions.append({"type": stype, "text": text})
    score_summary = _score_from_rows(rows)
    score = score_summary["final_score"]
    level = score_summary["recommend_level"]
    return {
        "position_id": position["id"],
        "final_score": score,
        "recommend_level": level,
        "rows": rows,
        "dims": score_summary,
        "suggestions": suggestions,
        "analysis": analysis,
        "raw": raw,
        "tokens": tokens,
        "latency_ms": int((time.time() - start) * 1000),
        "user_prompt": user,
        "input_signature": signature,
        "reused_from_result_id": None,
    }


def execute_run(run_id: int):
    """队列 worker 入口：完整执行一次匹配 run。"""
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM match_runs WHERE id=?", (run_id,)).fetchone()
        candidate = conn.execute(
            "SELECT * FROM candidates WHERE id=?", (run["candidate_id"],)
        ).fetchone()
        position_ids = json.loads(run["position_ids_json"])

        positions = _load_positions(conn, position_ids)
        if not positions:
            raise ValueError("没有可用岗位")
        resume_text = candidate["resume_text"]
        cand_dict = dict(candidate)

        run_user_id = run["created_by_user_id"] if "created_by_user_id" in run.keys() else None
        general = prompt_composer.resolve_active_prompt(conn, "general", user_id=run_user_id)
        detail_system = prompt_composer.compose_system(general, "detail")

        _set_status(run_id, "detailing")
        detail_map = {}
        detail_prompts = {}
        detail_errors = {}
        with ThreadPoolExecutor(max_workers=settings.llm_max_concurrency) as pool:
            futures = {
                pool.submit(_detail_one, p, resume_text, detail_system, run_id, cand_dict, run_user_id): p
                for p in positions
            }
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    result = fut.result()
                except Exception as e:  # noqa: BLE001 单岗位失败降级，不拖垮整个 run
                    detail_errors[p["id"]] = f"{p.get('title') or p['id']}：明细分析失败：{str(e)[:120]}"
                    continue
                detail_map[result["position_id"]] = result
                detail_prompts[str(result["position_id"])] = result.pop("user_prompt")

        # 落库
        result_ids = []
        for p in positions:
            detail = detail_map.get(p["id"])
            cur = conn.execute(
                "INSERT INTO match_results (run_id, position_id, exclude_reason, final_score, recommend_level,"
                " detail_rows_json, dims_json, suggestions_json, raw_llm_output,"
                " input_signature, reused_from_result_id, tokens, latency_ms)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    p["id"],
                    "" if detail else detail_errors.get(p["id"], "明细分析未完成"),
                    detail["final_score"] if detail else None,
                    detail["recommend_level"] if detail else None,
                    json.dumps(detail["rows"], ensure_ascii=False) if detail else "",
                    json.dumps(detail["dims"], ensure_ascii=False) if detail and detail["dims"] else "",
                    json.dumps(detail["suggestions"], ensure_ascii=False) if detail else "",
                    detail["raw"] if detail else "",
                    detail.get("input_signature", "") if detail else "",
                    detail.get("reused_from_result_id") if detail else None,
                    detail["tokens"] if detail else 0,
                    detail["latency_ms"] if detail else 0,
                ),
            )
            result_ids.append(cur.lastrowid)
        snapshot = {
            "detail_system": detail_system,
            "detail_user_prompts": detail_prompts,
            "score_rule": scoring.SCORING_POLICY_VERSION,
            "input_policy": "resume_structured_summary_plus_relevant_excerpt; jd_structured_conditions_plus_short_excerpt; active_prompt_versions_only",
        }
        conn.execute(
            "UPDATE match_runs SET prompt_snapshot_json=?, model=? WHERE id=?",
            (json.dumps(snapshot, ensure_ascii=False), settings.openai_model, run_id),
        )
        conn.commit()
        failed_count = len(positions) - len(detail_map)
        if not detail_map:
            status = "failed"
            error = _detail_error_summary(detail_errors, len(positions))
        elif failed_count:
            status = "done_with_errors"
            error = _detail_error_summary(detail_errors, len(positions))
        else:
            status = "done"
            error = ""
        _set_status(run_id, status, error=error)
        for result_id in result_ids:
            feishu_store.push_match_result(conn, result_id)
    except Exception as e:  # noqa: BLE001 run 级兜底，失败原因落库
        _set_status(run_id, "failed", error=str(e)[:1000])


def _detail_error_summary(detail_errors: dict, total_positions: int) -> str:
    errors = [str(value) for value in detail_errors.values() if value]
    if not errors:
        return "明细分析失败，未返回具体失败原因。"
    prefix = "全部岗位明细分析失败" if len(errors) >= total_positions else f"{len(errors)}/{total_positions} 个岗位明细分析失败"
    return f"{prefix}：" + "；".join(errors[:3])


def run_progress(conn, run) -> dict:
    """给批量状态查询接口的进度信息。"""
    position_ids = loads_or(run["position_ids_json"], [])
    result_count = conn.execute(
        "SELECT COUNT(*) AS c FROM match_results WHERE run_id=?", (run["id"],)
    ).fetchone()["c"]
    valid_count = conn.execute(
        "SELECT COUNT(*) AS c FROM match_results WHERE run_id=? AND final_score IS NOT NULL",
        (run["id"],),
    ).fetchone()["c"]
    failed_count = conn.execute(
        "SELECT COUNT(*) AS c FROM match_results WHERE run_id=? AND final_score IS NULL AND exclude_reason != ''",
        (run["id"],),
    ).fetchone()["c"]
    return {
        "total_positions": len(position_ids),
        "results_saved": result_count,
        "valid_results": valid_count,
        "failed_results": failed_count,
        "status": run["status"],
    }
