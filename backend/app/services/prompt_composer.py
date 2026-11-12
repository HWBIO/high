"""三层提示词组装：通用（DB）+ 输出骨架（代码固定）+ 公司/岗位层（DB）。

纯函数，不做 IO；调用方负责查库。"""
import json
import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
MAX_RESUME_SUMMARY_CHARS = 5000
MAX_RELEVANT_EXCERPT_CHARS = 3000
MAX_JD_EXCERPT_CHARS = 12000


def _stage_skeleton(stage: str) -> str:
    return (PROMPTS_DIR / f"{stage}_stage.md").read_text(encoding="utf-8")


def _few_shot() -> str:
    return (PROMPTS_DIR / "few_shot.md").read_text(encoding="utf-8")


def compose_system(general_prompt: str, stage: str, with_few_shot: bool = True) -> str:
    """system = 通用提示词 + 固定输出骨架（+ few-shot，仅明细阶段）。"""
    parts = [general_prompt.strip(), _stage_skeleton(stage).strip()]
    if with_few_shot and stage == "detail":
        parts.append(_few_shot().strip())
    return "\n\n---\n\n".join(p for p in parts if p)


def candidate_extra_block(candidate: dict) -> str:
    """候选人结构化补充信息（薪资/城市等），有值才输出；用于逐项条件判断。"""
    if not candidate:
        return ""
    bits = []
    for key, label in (
        ("city", "所在城市"),
        ("current_salary", "当前薪资"),
        ("expected_salary", "期望薪资"),
        ("tags", "标签"),
    ):
        val = (candidate.get(key) or "").strip() if isinstance(candidate, dict) else ""
        if val:
            bits.append(f"{label}：{val}")
    return "\n".join(bits)


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n……（已截取，完整原文保存在系统中）"


def _loads_json(text: str, default):
    try:
        return json.loads(text or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _section_summary(candidate: dict, resume_text: str) -> str:
    sections = _loads_json((candidate or {}).get("resume_struct_json", ""), [])
    if not isinstance(sections, list) or not sections:
        return _clip(resume_text, MAX_RESUME_SUMMARY_CHARS)
    raw_section = next(
        (s for s in sections if s.get("title") in ("原始内容", "全文", "简历原文")),
        None,
    )
    if raw_section:
        raw_lines = [str(line).strip() for line in raw_section.get("lines", []) if str(line).strip()]
        if raw_lines:
            return _clip(f"### {raw_section.get('title')}\n" + "\n".join(raw_lines), MAX_RESUME_SUMMARY_CHARS)
    preferred = ("个人信息", "求职方向", "教育背景", "工作经历", "实习经历", "项目经历", "技能证书")
    parts = []
    for title in preferred:
        section = next((s for s in sections if s.get("title") == title), None)
        if not section:
            continue
        lines = [str(line).strip() for line in section.get("lines", []) if str(line).strip()]
        if not lines:
            continue
        max_lines = 24 if title in ("工作经历", "项目经历") else 8
        parts.append(f"### {title}\n" + "\n".join(lines[:max_lines]))
    if not parts:
        return _clip(resume_text, MAX_RESUME_SUMMARY_CHARS)
    return _clip("\n\n".join(parts), MAX_RESUME_SUMMARY_CHARS)


def _keywords_from_position(position: dict, company_prompt: str, position_prompt: str) -> list[str]:
    struct = position.get("jd_struct") or {}
    source_parts = []
    for key in ("position_name", "years", "education", "city", "salary"):
        val = struct.get(key)
        if val:
            source_parts.append(str(val))
    for key in ("skills_required", "skills_preferred", "hard_rules", "requirements"):
        vals = struct.get(key) or []
        if isinstance(vals, list):
            source_parts.extend(str(v) for v in vals)
    source_parts.extend([
        position.get("title") or "",
        position.get("hard_requirements") or "",
        position.get("nice_to_have") or "",
        company_prompt or "",
        position_prompt or "",
    ])
    text = "\n".join(source_parts)
    candidates = re.findall(r"[A-Za-z][A-Za-z0-9+#./-]{1,}|[\u4e00-\u9fff]{2,8}", text)
    stopwords = {
        "岗位", "公司", "要求", "能力", "经验", "负责", "相关", "优先", "必须", "熟悉",
        "具备", "以上", "以下", "工作", "项目", "业务", "候选人", "匹配", "条件",
    }
    result = []
    seen = set()
    for word in candidates:
        key = word.lower()
        if key in seen or word in stopwords:
            continue
        seen.add(key)
        result.append(word)
        if len(result) >= 40:
            break
    return result


def _relevant_resume_excerpt(resume_text: str, keywords: list[str]) -> str:
    if not resume_text.strip() or not keywords:
        return ""
    lines = [line.strip() for line in resume_text.splitlines() if line.strip()]
    hits = []
    lowered_keywords = [kw.lower() for kw in keywords if len(kw.strip()) >= 2]
    for line in lines:
        lowered = line.lower()
        if any(kw in lowered for kw in lowered_keywords):
            hits.append(line)
        if len("\n".join(hits)) >= MAX_RELEVANT_EXCERPT_CHARS:
            break
    return _clip("\n".join(hits), MAX_RELEVANT_EXCERPT_CHARS)


def candidate_resume_block(candidate: dict, resume_text: str, position: dict, company_prompt: str, position_prompt: str) -> str:
    summary = _section_summary(candidate or {}, resume_text)
    keywords = _keywords_from_position(position, company_prompt, position_prompt)
    excerpt = _relevant_resume_excerpt(resume_text, keywords)
    parts = ["## 候选人结构化摘要（由上传简历解析生成，匹配时优先使用）\n" + summary]
    if excerpt:
        parts.append("## 与本岗位相关的简历原文片段（用于核验证据，不是完整简历）\n" + excerpt)
    return "\n\n".join(parts)


def jd_requirements_block(position: dict) -> str:
    struct = position.get("jd_struct") or {}
    parts = []
    fields = []
    for key, label in (
        ("years", "经验年限"),
        ("education", "学历"),
        ("city", "城市"),
        ("salary", "薪资"),
    ):
        val = struct.get(key)
        if val:
            fields.append(f"{label}：{val}")
    if struct.get("skills_required"):
        fields.append("必备技能：" + "、".join(map(str, struct["skills_required"])))
    if struct.get("skills_preferred"):
        fields.append("优先技能：" + "、".join(map(str, struct["skills_preferred"])))
    if struct.get("hard_rules"):
        fields.append("硬性限制：" + "、".join(map(str, struct["hard_rules"])))
    if fields:
        parts.append(
            "## JD结构化索引\n"
            + "\n".join(fields)
        )
    requirements = struct.get("requirements") or []
    if requirements:
        parts.append(
            "## JD结构化要求列表\n"
            + "\n".join(f"{i + 1}. {r}" for i, r in enumerate(requirements))
        )
    jd_raw = (position.get("jd_raw") or "").strip()
    if jd_raw:
        parts.append(
            "## 岗位JD原文底稿\n"
            + _clip(jd_raw, MAX_JD_EXCERPT_CHARS)
        )
    return "\n\n".join(parts)


def compose_detail_user(
    position: dict,
    resume_text: str,
    company_prompt: str = "",
    position_prompt: str = "",
    candidate: dict = None,
    label_context: str = "",
) -> str:
    """明细阶段 user 消息：公司层 → 岗位层 → JD 结构化 → JD 原文 → 顾问备注 → 简历。"""
    parts = []
    if company_prompt.strip():
        parts.append("## 公司背景与偏好\n" + company_prompt.strip())
    if position_prompt.strip():
        parts.append(
            "## 岗位补充提示（先区分真正关键门槛与普通能力要求）\n" + position_prompt.strip()
        )
    if label_context.strip():
        parts.append(label_context.strip())
    pos_info = (
        "## 岗位信息\n"
        f"岗位名称：{position.get('title', '')}\n"
        f"公司：{position.get('company_name') or '未指定'}"
    )
    if (position.get("salary_range") or "").strip():
        pos_info += f"\n月薪范围（岗位预算）：{position['salary_range'].strip()}"
    parts.append(pos_info)
    jd_block = jd_requirements_block(position)
    if jd_block:
        parts.append(jd_block)
    notes = (position.get("consultant_notes") or "").strip()
    if notes:
        parts.append("## 顾问备注\n" + notes)
    extra = candidate_extra_block(candidate or {})
    if extra:
        parts.append("## 候选人补充信息（顾问录入，简历之外的事实）\n" + extra)
    parts.append(candidate_resume_block(candidate or {}, resume_text, position, company_prompt, position_prompt))
    return "\n\n".join(parts)


def resolve_active_prompt(conn, layer: str, company_id=None, position_id=None, user_id=None) -> str:
    """查某个 scope 的 active 提示词内容，没有则空字符串。"""
    if layer == "general":
        if user_id:
            pref = conn.execute(
                "SELECT general_prompt_id, general_prompt_source FROM user_prompt_preferences"
                " WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if pref and pref["general_prompt_id"]:
                row = conn.execute(
                    "SELECT content FROM prompts WHERE id=? AND layer='general'",
                    (pref["general_prompt_id"],),
                ).fetchone()
                if row:
                    return row["content"]
        row = conn.execute(
            "SELECT content FROM prompts WHERE layer='general' AND scope='company_default' "
            "ORDER BY is_active DESC, version DESC LIMIT 1"
        ).fetchone()
    elif layer == "company":
        if not company_id:
            return ""
        row = conn.execute(
            "SELECT content FROM prompts WHERE layer='company' AND company_id=? "
            "ORDER BY is_active DESC, version DESC LIMIT 1",
            (company_id,),
        ).fetchone()
    else:
        if not position_id:
            return ""
        row = conn.execute(
            "SELECT content FROM prompts WHERE layer='position' AND position_id=? "
            "ORDER BY is_active DESC, version DESC LIMIT 1",
            (position_id,),
        ).fetchone()
    return row["content"] if row else ""
