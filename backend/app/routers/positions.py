import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from openpyxl import load_workbook
from pydantic import BaseModel

from ..db import company_display_expr, get_conn, loads_or
from ..services import document_importer, feishu_store, llm, match_result_cleanup, operation_log
from ..services.positions_helpers import ensure_company
from .auth import require_position_operator

router = APIRouter(prefix="/api/positions", tags=["positions"])

PROJECT_STATUS = ("未开始", "进行中", "已结束")
PROJECT_STATUS_ALIASES = {
    "待开始": "未开始",
    "待启动": "未开始",
    "进展中": "进行中",
    "Live": "进行中",
    "live": "进行中",
    "已暂停": "已结束",
    "已完成": "已结束",
    "已取消": "已结束",
}


def _normalize_project_status(value: str) -> str:
    text = str(value or "").strip()
    return PROJECT_STATUS_ALIASES.get(text, text or "未开始")


def _safe_upload_display_name(filename: str) -> str:
    return Path(str(filename or "未命名文件").replace("\\", "/")).name or "未命名文件"


def _position_xlsx_temp_path(upload_dir: Path) -> Path:
    return upload_dir / f"import_xlsx_{int(time.time())}_{uuid.uuid4().hex[:8]}.xlsx"

# 品聘式项目字段（无专列的收进 project_json）
PROJECT_KEYS = (
    "contact", "fee", "priority", "industry", "function", "city", "address",
    "guarantee_months", "start_date", "end_date",
)

COMPANY_FIELD_NAMES = (
    "公司", "客户", "客户公司", "岗位公司", "公司名称",
    "客户名称", "客户名", "企业名称", "公司全称", "所属客户",
)

HARD_STRUCT_KEYS = (
    "language", "company_background", "gender", "arrival_time",
    "must_skills", "excluded_companies", "vetoes",
)

POSITION_IMPORT_SYSTEM = """你是猎头岗位文件解析助手。请从用户上传的岗位文档中提取岗位信息。

只输出合法 JSON 对象，不要任何其他文字：

{
  "positions": [
    {
      "seq_no": "序号，没有则空字符串",
      "company": "客户公司的营业执照全称，没有则空字符串",
      "title": "岗位/项目名称",
      "headcount": 1,
      "salary_range": "月薪范围，没有则空字符串",
      "project_status": "未开始",
      "consultant_notes": "顾问备注/客户特殊要求，没有则空字符串",
      "hard_requirements": "硬性要求原文要点，没有则空字符串",
      "nice_to_have": "优先条件/加分项原文要点，没有则空字符串",
      "unstructured_summary": "原文中会影响匹配但未能准确填入项目字段或匹配硬条件的要求摘要，没有则空字符串",
      "jd_raw": "该岗位完整 JD/职责/要求原文，保留原文关键内容",
      "project": {
        "contact": "主要联系人，没有则空字符串",
        "fee": "项目费用/服务费，没有则空字符串",
        "priority": "项目优先级，没有则空字符串",
        "industry": "行业，没有则空字符串",
        "function": "职能，没有则空字符串",
        "city": "城市，没有则空字符串",
        "address": "工作地址，没有则空字符串",
        "guarantee_months": "保证期，没有则空字符串",
        "start_date": "开始日期，没有则空字符串",
        "end_date": "结束日期，没有则空字符串"
      },
      "hard_struct": {
        "language": "语言能力",
        "company_background": "名企背景/公司背景要求",
        "gender": "性别要求",
        "arrival_time": "到岗时间",
        "must_skills": ["必须技能"],
        "excluded_companies": ["排除公司"],
        "vetoes": ["一票否决项"]
      }
    }
  ]
}

规则：
- 文档中有多个岗位时必须全部提取。
- title 和 jd_raw 是最低必需字段；缺失时也不要编造。
- company 必须优先提取营业执照上的公司全称；原文只有品牌名或简称时如实填写，不要自行扩写。
- title 只填写岗位/项目名称，不要包含客户公司名；如果原文写成“岗位名（公司名）”，请把括号里的公司名放到 company 字段，title 只保留“岗位名”。如果括号内容是方向、职级或业务线（如“商业化方向”“专家岗位”），则可保留在 title 中。
- 原文中的“客户、客户公司、客户名称、企业名称、公司名称、公司全称、所属客户”都视为 company。
- project_status 只能是 未开始/进行中/已结束，无法判断填 未开始。
- 开始日期和结束日期只有原文出现明确日期时才填写，格式统一 YYYY-MM-DD；不要根据当前时间推断。
- AI 解析出的字段只是高优先级结构化索引，不能替代 jd_raw；jd_raw 必须尽量保留完整原文。
- 如果原文有要求无法稳定落入 project 或 hard_struct，例如软性偏好、复杂业务背景、隐含交付要求，请写入 unstructured_summary，提醒顾问人工补充到匹配硬条件或岗位提示词；不要重复已经结构化的字段。
- 不要编造文档里不存在的公司、薪资、城市、技能。"""


class PositionIn(BaseModel):
    title: str
    jd_raw: str = ""
    company_id: Optional[int] = None
    company: str = ""
    seq_no: str = ""
    consultant_notes: str = ""
    salary_range: str = ""
    hard_requirements: str = ""
    nice_to_have: str = ""
    headcount: int = 1
    project_status: str = "未开始"
    consultant: str = ""
    is_project: int = 0
    project: dict = None
    hard_struct: dict = None


class PositionParseIn(BaseModel):
    text: str


class PositionUpdate(BaseModel):
    title: str = None
    jd_raw: str = None
    consultant_notes: str = None
    status: str = None
    company_id: Optional[int] = None
    company: str = None
    source: str = None
    headcount: int = None
    salary_range: str = None
    hard_requirements: str = None
    nice_to_have: str = None
    project_status: str = None
    n_talents: int = None
    n_recommend: int = None
    n_interview: int = None
    n_offer: int = None
    consultant: str = None
    is_project: int = None
    project: dict = None
    hard_struct: dict = None


def _compose_jd_from_project(body: PositionIn) -> str:
    """新增岗位没填 JD 时，用项目要求字段拼一份可供匹配的岗位要求文本。"""
    p = body.project or {}
    labels = (
        ("contact", "主要联系人"),
        ("fee", "项目费用"),
        ("priority", "项目优先级"),
        ("industry", "行业"),
        ("function", "职能"),
        ("city", "工作城市"),
        ("address", "工作地址"),
        ("guarantee_months", "保证期"),
        ("start_date", "开始日期"),
        ("end_date", "结束日期"),
    )
    lines = [f"{label}：{p[k]}" for k, label in labels if (p.get(k) or "").strip()]
    if body.title.strip():
        lines.insert(0, f"岗位名称：{body.title.strip()}")
    if body.salary_range.strip():
        lines.append(f"月薪范围：{body.salary_range.strip()}")
    if body.consultant_notes.strip():
        lines.append("顾问备注：\n" + body.consultant_notes.strip())
    struct_text = _struct_to_prompt(_clean_struct(body.hard_struct))
    if struct_text:
        lines.append("匹配硬条件：\n" + struct_text)
    if body.hard_requirements.strip():
        lines.append("硬性要求：\n" + body.hard_requirements.strip())
    if body.nice_to_have.strip():
        lines.append("优先条件：\n" + body.nice_to_have.strip())
    return "## 岗位要求（由项目字段生成）\n" + "\n".join(lines) if lines else ""


def _validate_project_dates(project: dict):
    start_date = str((project or {}).get("start_date") or "").strip()
    end_date = str((project or {}).get("end_date") or "").strip()
    if not start_date or not end_date:
        return
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date):
        return
    if end_date <= start_date:
        raise HTTPException(400, "结束日期必须晚于开始日期")


def _normalize_project_dates(project: dict) -> tuple[dict, list[str]]:
    cleaned = dict(project or {})
    warnings = []
    for key in ("start_date", "end_date"):
        value = str(cleaned.get(key) or "").strip()
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            cleaned[key] = ""
            warnings.append(f"{key} 不是 YYYY-MM-DD 格式，已忽略")
    try:
        _validate_project_dates(cleaned)
    except HTTPException:
        cleaned["end_date"] = ""
        warnings.append("结束日期未晚于开始日期，已忽略结束日期")
    return cleaned, warnings


def _positive_int(value, default: int = 1) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def _clean_struct(data: Optional[dict]) -> dict:
    if not data:
        return {}
    cleaned = {}
    for key in HARD_STRUCT_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            items = [str(v).strip() for v in value if str(v).strip()]
            if items:
                cleaned[key] = items
        elif value is not None and str(value).strip():
            cleaned[key] = str(value).strip()
    return cleaned


def _struct_to_prompt(data: dict) -> str:
    if not data:
        return ""
    labels = {
        "language": "语言能力",
        "company_background": "公司背景",
        "gender": "性别要求",
        "arrival_time": "到岗时间",
        "must_skills": "必须技能",
        "excluded_companies": "排除公司",
        "vetoes": "一票否决",
    }
    lines = []
    for key in HARD_STRUCT_KEYS:
        value = data.get(key)
        if not value:
            continue
        text = "、".join(value) if isinstance(value, list) else str(value)
        lines.append(f"- {labels[key]}：{text}")
    return "\n".join(lines)


def _auto_position_prompt(conn, position_id: int, hard: str, nice: str, hard_struct: Optional[dict] = None):
    """硬性要求/优先条件字段变化时，自动生成岗位专属提示词新版本。

    顾问只填匹配硬条件即可，不必理解「提示词」概念；两字段都空则不生成。
    """
    parts = []
    struct_text = _struct_to_prompt(hard_struct or {})
    if struct_text:
        parts.append(
            "匹配硬条件（先区分真正关键门槛与普通要求；只有明确一票否决项才作为 blocker，"
            "其余按 core/relevance/preference/risk 拆解）：\n" + struct_text
        )
    if hard.strip():
        parts.append(
            "岗位核心要求（逐条核对，但不要默认都是 blocker；主线能力放 core，场景相似放 relevance，"
            "加分项放 preference；缺失但可由相近经历部分支撑时标记待确认）：\n" + hard.strip()
        )
    if nice.strip():
        parts.append("优先条件（逐条作为优先条件池判断；满足体现竞争力，不满足不作为硬性风险）：\n" + nice.strip())
    if not parts:
        return
    content = "\n\n".join(parts)
    scope = ("position", None, position_id)
    old = conn.execute(
        "SELECT MAX(version) AS v, MAX(CASE WHEN is_active=1 THEN content END) AS cur"
        " FROM prompts WHERE layer=? AND company_id IS ? AND position_id IS ?",
        scope,
    ).fetchone()
    if (old["cur"] or "") == content:
        return  # 内容没变不生成新版本
    conn.execute(
        "UPDATE prompts SET is_active=0 WHERE layer=? AND company_id IS ? AND position_id IS ? AND is_active=1",
        scope,
    )
    conn.execute(
        "INSERT INTO prompts (layer, position_id, content, version, updated_by)"
        " VALUES ('position', ?, ?, ?, 'auto:岗位字段')",
        (position_id, content, (old["v"] or 0) + 1),
    )


def _normalize_parsed_position(item: dict) -> tuple[dict, list[str]]:
    project = {
        key: str((item.get("project") or {}).get(key) or "").strip()
        for key in PROJECT_KEYS
    }
    project, warnings = _normalize_project_dates(project)
    project_status = _normalize_project_status(item.get("project_status"))
    if project_status not in PROJECT_STATUS:
        project_status = "未开始"
        warnings.append("项目状态无法识别，已按未开始处理")
    hard_struct = _clean_struct(item.get("hard_struct") or {})
    return {
        "seq_no": str(item.get("seq_no") or "").strip(),
        "company": str(item.get("company") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "headcount": _positive_int(item.get("headcount")),
        "salary_range": str(item.get("salary_range") or "").strip(),
        "project_status": project_status,
        "consultant_notes": str(item.get("consultant_notes") or "").strip(),
        "hard_requirements": str(item.get("hard_requirements") or "").strip(),
        "nice_to_have": str(item.get("nice_to_have") or "").strip(),
        "unstructured_summary": str(item.get("unstructured_summary") or "").strip(),
        "jd_raw": str(item.get("jd_raw") or "").strip(),
        "project": project,
        "hard_struct": hard_struct,
    }, warnings


def _xlsx_cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").strip()


def _normalize_header(value: str) -> str:
    return value.replace(" ", "").replace("\n", "").strip()


def _header_score(headers: list[str]) -> int:
    canonical_groups = (
        ("招聘岗位名称", "岗位名称", "职位名称", "项目名称"),
        ("岗位职责", "职责", "工作职责"),
        ("任职要求", "岗位要求", "要求"),
        ("工作地点", "城市", "工作城市"),
        ("月薪", "月薪范围", "薪资范围", "薪资", "年薪范围"),
        COMPANY_FIELD_NAMES,
    )
    return sum(1 for group in canonical_groups if any(name in headers for name in group))


def _find_xlsx_header(rows: list[tuple]) -> tuple[int, dict[str, int]]:
    best_index = -1
    best_headers: list[str] = []
    best_score = 0
    for index, row in enumerate(rows[:20]):
        headers = [_normalize_header(_xlsx_cell(cell)) for cell in row]
        score = _header_score(headers)
        if score > best_score:
            best_index = index
            best_headers = headers
            best_score = score
    if best_score < 2:
        return -1, {}
    return best_index, {header: index for index, header in enumerate(best_headers) if header}


def _company_from_title(title: str) -> str:
    match = re.search(r"[（(]([^（）()]+)[）)]\s*$", title.strip())
    if not match:
        return ""
    value = match.group(1).strip()
    company_signals = (
        "公司", "有限", "科技", "STUDIO", "Studio", "Netconcepts", "耐特康赛",
        "超线性", "库迪", "咖啡", "拾异", "迦游",
    )
    if not any(signal in value for signal in company_signals):
        return ""
    return value


def _company_from_source_name(name: str) -> str:
    text = _xlsx_cell(name)
    if not text:
        return ""
    text = re.sub(r"\.[A-Za-z0-9]+$", "", text)
    text = re.sub(r"^(客户公司|客户名称|公司名称|企业名称|所属客户|客户|公司|全称)[:：]", "", text).strip()
    text = re.sub(r"(?i)(全部)?岗位\s*jd", "", text)
    text = re.sub(r"(?i)(岗位|职位)?\s*jd", "", text)
    text = re.sub(r"(人才)?招聘手册", "", text)
    text = re.sub(r"(招聘)?(岗位|职位)?(需求|清单|列表|表)", "", text)
    text = re.sub(r"最新|脱敏|全部|项目", "", text)
    text = re.sub(r"\d{1,4}([./_-]\d{1,2}){0,2}", "", text)
    text = re.sub(r"[\s_【】\[\]（）()，,。.-]+", "", text)
    generic = {"sheet", "sheet1", "工作1", "工作表1", "positions", "position", "jobs", "job"}
    if not text or text.lower() in generic or len(text) > 30:
        return ""
    return text


def _strip_company_from_title(title: str, company: str = "") -> str:
    cleaned = title.strip()
    candidates = [company.strip()] if company.strip() else []
    inferred = _company_from_title(cleaned)
    if inferred:
        candidates.append(inferred)

    def normalize(value: str) -> str:
        return re.sub(r"[\s（）()，,、·\-_/]+", "", value).lower()

    for value in dict.fromkeys(candidates):
        for left, right in (("（", "）"), ("(", ")")):
            suffix = f"{left}{value}{right}"
            if cleaned.endswith(suffix):
                return cleaned[: -len(suffix)].strip()
        match = re.search(r"[（(]([^（）()]+)[）)]\s*$", cleaned)
        if match and normalize(match.group(1)) == normalize(value):
            return cleaned[: match.start()].strip()
    return cleaned


def _section_company(row, pick) -> str:
    seq = pick(row, "序号")
    city = pick(row, "工作地点", "城市", "工作城市")
    title = pick(row, "招聘岗位名称", "岗位名称", "职位名称", "项目名称")
    salary = pick(row, "月薪", "月薪范围", "薪资范围", "薪资", "年薪范围")
    if seq or salary:
        return ""
    text = city or title
    if not text:
        return ""
    full_name_prefix = "全称："
    if title.startswith(full_name_prefix):
        return _company_from_source_name(city or title.replace(full_name_prefix, "").strip())
    return _company_from_source_name(text)


HANDBOOK_META_FIELDS = ("HC", "地点", "经验参考", "年限", "核心关键词")
HANDBOOK_SECTION_FIELDS = (
    "岗位职责",
    "我们希望你具备",
    "加分项",
    "可以放宽",
    "重点方向",
    "我们真正看重什么",
    "我们接受的非标准候选人",
)
HANDBOOK_MAJOR_SECTIONS = (
    "招聘概览",
    "关于",
    "人才观",
    "匹配度",
    "我们正在寻找的人",
    "我们如何看一个候选人",
    "面试方式",
    "招聘流程",
    "长期同行",
)


def _clean_handbook_lines(text: str) -> list[str]:
    normalized = text.replace("\u2028", "\n").replace("\u2029", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def _looks_like_position_title(line: str) -> bool:
    if len(line) < 4 or len(line) > 140:
        return False
    if any(marker in line for marker in HANDBOOK_MAJOR_SECTIONS):
        return False
    signals = (
        "/", "工程师", "经理", "主管", "负责人", "销售", "产品", "财务",
        "HRBP", "Talent", "Lead", "Manager", "Engineer", "Sales", "Product",
        "Supply Chain", "Core Talent",
    )
    return any(signal in line for signal in signals)


def _is_handbook_position_start(lines: list[str], index: int) -> bool:
    if not re.fullmatch(r"\d{1,2}", lines[index]):
        return False
    if index + 1 >= len(lines) or not _looks_like_position_title(lines[index + 1]):
        return False
    window = lines[index + 1:index + 14]
    return any(line in HANDBOOK_META_FIELDS or line in HANDBOOK_SECTION_FIELDS for line in window)


def _is_handbook_major_section(line: str) -> bool:
    return bool(re.match(r"^\d{1,2}｜", line)) and any(marker in line for marker in HANDBOOK_MAJOR_SECTIONS)


def _extract_handbook_company(lines: list[str], filename: str) -> str:
    brand_lines = []
    for line in lines[:16]:
        if _is_handbook_major_section(line):
            break
        if any(marker in line for marker in ("人才招聘手册", "岗位说明", "关于我们", "我们在找什么样的人")):
            continue
        if re.search(r"\d{4}[./-]\d{1,2}|\d{4}$", line):
            continue
        if len(line) <= 40:
            brand_lines.append(line)
        if len(brand_lines) >= 2:
            break
    if len(brand_lines) >= 2:
        return f"{brand_lines[0]} / {brand_lines[1]}"
    if brand_lines:
        return brand_lines[0]
    return _company_from_source_name(filename)


def _collect_until_heading(lines: list[str], start: int, headings: tuple[str, ...]) -> tuple[str, int]:
    parts = []
    index = start
    while index < len(lines):
        if lines[index] in headings or _is_handbook_position_start(lines, index) or _is_handbook_major_section(lines[index]):
            break
        parts.append(lines[index])
        index += 1
    return "\n".join(parts).strip(), index


def _parse_headcount(value: str) -> int:
    match = re.search(r"\d+", value or "")
    return _positive_int(match.group(0) if match else 1)


def _parse_city(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    for city in ("北京", "上海", "深圳", "广州", "杭州", "成都", "厦门"):
        if city in text:
            return city
    return re.split(r"[｜|；;，,、/]", text, maxsplit=1)[0].strip()


def _parse_handbook_block(lines: list[str], start: int, end: int, company: str) -> dict:
    seq_no = lines[start].lstrip("0") or lines[start]
    title = re.sub(r"\s*/\s*", " / ", lines[start + 1]).strip()
    index = start + 2
    subtitle_parts = []
    meta = {}
    sections = {}
    all_headings = HANDBOOK_META_FIELDS + HANDBOOK_SECTION_FIELDS
    while index < end:
        line = lines[index]
        if line in HANDBOOK_META_FIELDS and index + 1 < end:
            meta[line] = lines[index + 1]
            index += 2
            continue
        if line in HANDBOOK_SECTION_FIELDS:
            content, next_index = _collect_until_heading(lines, index + 1, all_headings)
            sections[line] = content
            index = next_index
            continue
        if line not in all_headings:
            subtitle_parts.append(line)
        index += 1

    responsibility = sections.get("岗位职责", "")
    requirement = "\n\n".join(
        part for part in (
            sections.get("我们希望你具备", ""),
            sections.get("我们真正看重什么", ""),
        )
        if part
    )
    nice = "\n\n".join(
        part for part in (
            sections.get("加分项", ""),
            sections.get("可以放宽", ""),
            sections.get("我们接受的非标准候选人", ""),
        )
        if part
    )
    notes = []
    if subtitle_parts:
        notes.append("说明：" + "；".join(subtitle_parts[:3]))
    for label in ("经验参考", "年限", "核心关键词", "地点", "HC"):
        if meta.get(label):
            notes.append(f"{label}：{meta[label]}")
    if sections.get("重点方向"):
        notes.append("重点方向：\n" + sections["重点方向"])

    jd_parts = [f"岗位名称：{title}"]
    for label in ("HC", "地点", "经验参考", "年限", "核心关键词"):
        if meta.get(label):
            jd_parts.append(f"{label}：{meta[label]}")
    for label in HANDBOOK_SECTION_FIELDS:
        if sections.get(label):
            jd_parts.append(f"{label}：\n{sections[label]}")

    return {
        "seq_no": seq_no,
        "company": company,
        "title": title,
        "headcount": _parse_headcount(meta.get("HC", "")),
        "salary_range": "",
        "project_status": "未开始",
        "consultant_notes": "\n".join(notes).strip(),
        "hard_requirements": requirement,
        "nice_to_have": nice,
        "unstructured_summary": "",
        "jd_raw": "\n\n".join(part for part in jd_parts if part).strip(),
        "project": {"city": _parse_city(meta.get("地点", ""))},
        "hard_struct": {},
    }


def _extract_positions_from_handbook_text(text: str, filename: str = "") -> list[dict]:
    """解析 Word/PDF/TXT 招聘手册中按编号排列的岗位块，避免整篇长 JSON 被模型截断。"""
    lines = _clean_handbook_lines(text)
    if not lines:
        return []
    starts = [index for index in range(len(lines)) if _is_handbook_position_start(lines, index)]
    if not starts:
        return []
    company = _extract_handbook_company(lines, filename)
    items = []
    for order, start in enumerate(starts):
        next_start = starts[order + 1] if order + 1 < len(starts) else len(lines)
        end = next_start
        for index in range(start + 2, next_start):
            if _is_handbook_major_section(lines[index]):
                end = index
                break
        item = _parse_handbook_block(lines, start, end, company)
        if item["title"] and item["jd_raw"]:
            items.append(item)
    return items


def _extract_positions_from_xlsx(path: Path) -> list[dict]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    items: list[dict] = []
    try:
        workbook_company = _company_from_source_name(path.stem)
        for sheet in workbook.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            header_index, header_map = _find_xlsx_header(rows)
            if header_index < 0:
                continue
            sheet_company = _company_from_source_name(sheet.title) or workbook_company

            def pick(row, *names: str) -> str:
                for name in names:
                    index = header_map.get(name)
                    if index is not None and index < len(row):
                        text = _xlsx_cell(row[index])
                        if text:
                            return text
                return ""

            current: Optional[dict] = None
            current_company = sheet_company
            for row in rows[header_index + 1:]:
                section_company = _section_company(row, pick)
                if section_company:
                    current_company = section_company
                    continue
                seq = pick(row, "序号")
                title = pick(row, "招聘岗位名称", "岗位名称", "职位名称", "项目名称")
                city = pick(row, "工作地点", "城市", "工作城市")
                salary = pick(row, "月薪", "月薪范围", "薪资范围", "薪资", "年薪范围")
                language = pick(row, "语言能力", "语言要求", "外语要求")
                company_background = pick(row, "名企背景", "公司背景", "目标公司背景")
                gender = pick(row, "性别", "性别要求")
                responsibility = pick(row, "岗位职责", "职责", "工作职责")
                requirement = pick(row, "任职要求", "岗位要求", "要求")
                customer_note = pick(row, "客户备注", "客户要求")
                consultant_note = pick(row, "顾问后期备注", "后期备注", "顾问备注", "备注说明", "备注")
                if title:
                    if current:
                        items.append(current)
                    company = (
                        pick(row, *COMPANY_FIELD_NAMES)
                        or current_company
                        or _company_from_title(title)
                        or sheet_company
                    )
                    title = _strip_company_from_title(title, company)
                    current = {
                        "seq_no": seq,
                        "company": company,
                        "title": title,
                        "headcount": 1,
                        "salary_range": salary,
                        "project_status": "未开始",
                        "consultant_notes": consultant_note,
                        "hard_requirements": requirement,
                        "nice_to_have": "",
                        "jd_parts": [part for part in (responsibility, requirement) if part],
                        "project": {"city": city},
                        "hard_struct": {
                            "language": language,
                            "company_background": company_background,
                            "gender": gender,
                        },
                    }
                    if customer_note:
                        current["consultant_notes"] = "\n".join(
                            part for part in (customer_note, current["consultant_notes"]) if part
                        )
                elif current:
                    if responsibility:
                        current["jd_parts"].append(responsibility)
                    if requirement:
                        current["jd_parts"].append(requirement)
                        current["hard_requirements"] = "\n".join(
                            part for part in (current.get("hard_requirements", ""), requirement) if part
                        )
                    if customer_note:
                        current["consultant_notes"] = "\n".join(
                            part for part in (current.get("consultant_notes", ""), customer_note) if part
                        )
                    if consultant_note:
                        current["consultant_notes"] = "\n".join(
                            part for part in (current.get("consultant_notes", ""), consultant_note) if part
                        )
            if current:
                items.append(current)
    finally:
        workbook.close()
    for item in items:
        item["jd_raw"] = "\n".join(item.pop("jd_parts", [])).strip()
    return items


def _existing_position_for_import(conn, title: str, company_id: Optional[int]):
    if company_id:
        row = conn.execute(
            "SELECT id, company_id FROM positions WHERE title=? AND company_id=? AND status='active' ORDER BY id LIMIT 1",
            (title, company_id),
        ).fetchone()
        if row:
            return row
        row = conn.execute(
            "SELECT id, company_id FROM positions WHERE title=? AND company_id IS NULL AND status='active' ORDER BY id LIMIT 1",
            (title,),
        ).fetchone()
        if row:
            return row
    else:
        row = conn.execute(
            "SELECT id, company_id FROM positions WHERE title=? AND company_id IS NULL AND status='active' ORDER BY id LIMIT 1",
            (title,),
        ).fetchone()
        if row:
            return row
    same_title = conn.execute(
        "SELECT id, company_id FROM positions WHERE title=? AND status='active' ORDER BY id",
        (title,),
    ).fetchall()
    if len(same_title) == 1:
        # Repeated workbook imports may carry a stale source company label. If the title
        # resolves to exactly one active position, reuse it instead of creating duplicates.
        return same_title[0]
    return None


@router.get("")
def list_positions(status: str = "active"):
    conn = get_conn()
    company_expr = company_display_expr(conn, "c")
    where = "WHERE p.status=?" if status in ("active", "archived") else ""
    params = (status,) if where else ()
    rows = conn.execute(
        "SELECT p.id, p.company_id, p.seq_no, p.title, p.source, p.status, p.consultant_notes,"
        " p.headcount, p.salary_range, p.hard_requirements, p.nice_to_have,"
        " p.project_status, p.n_talents, p.n_recommend, p.n_interview, p.n_offer,"
        " p.consultant, p.is_project, p.hard_struct_json, p.project_json,"
        f" p.updated_at, {company_expr} AS company,"
        " (p.jd_struct_json != '') AS has_struct,"
        " EXISTS(SELECT 1 FROM prompts pr WHERE pr.layer='position' AND pr.position_id=p.id AND pr.is_active=1) AS has_position_prompt"
        f" FROM positions p LEFT JOIN companies c ON c.id=p.company_id {where}"
        " ORDER BY CAST(p.seq_no AS INTEGER), p.id",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/options")
def position_options(status: str = "active"):
    conn = get_conn()
    company_expr = company_display_expr(conn, "c")
    where = "WHERE p.status=?" if status in ("active", "archived") else ""
    params = (status,) if where else ()
    rows = conn.execute(
        f"SELECT p.id, p.seq_no, p.title, {company_expr} AS company"
        f" FROM positions p LEFT JOIN companies c ON c.id=p.company_id {where}"
        " ORDER BY CAST(p.seq_no AS INTEGER), p.id",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{position_id}")
def get_position(position_id: int):
    conn = get_conn()
    company_expr = company_display_expr(conn, "c")
    row = conn.execute(
        f"SELECT p.*, {company_expr} AS company FROM positions p"
        " LEFT JOIN companies c ON c.id=p.company_id WHERE p.id=?",
        (position_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "岗位不存在")
    pos = dict(row)
    pos["jd_struct"] = loads_or(pos.pop("jd_struct_json"), {})
    pos["hard_struct"] = loads_or(pos.pop("hard_struct_json"), {})
    return pos


@router.post("")
def create_position(body: PositionIn, _user=Depends(require_position_operator)):
    conn = get_conn()
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "项目名称不能为空")
    project_status = _normalize_project_status(body.project_status)
    if project_status not in PROJECT_STATUS:
        raise HTTPException(400, f"项目状态取值必须是 {PROJECT_STATUS}")
    company_id = body.company_id
    if company_id is not None:
        exists = conn.execute("SELECT id FROM companies WHERE id=?", (company_id,)).fetchone()
        if not exists:
            raise HTTPException(400, "客户不存在")
    else:
        company_id = ensure_company(conn, body.company)
    jd_raw = body.jd_raw.strip() or _compose_jd_from_project(body)
    if not jd_raw:
        raise HTTPException(400, "JD 原文和项目要求字段至少填一样，否则无法参与匹配")
    project = {k: str((body.project or {}).get(k) or "").strip() for k in PROJECT_KEYS}
    _validate_project_dates(project)
    hard_struct = _clean_struct(body.hard_struct)
    project_json = json.dumps(project, ensure_ascii=False)
    hard_struct_json = json.dumps(hard_struct, ensure_ascii=False)
    duplicate = conn.execute(
        "SELECT id FROM positions"
        " WHERE company_id IS ? AND title=? AND jd_raw=?"
        " AND project_json=? AND hard_struct_json=?"
        " AND created_at >= datetime('now','localtime','-10 seconds')"
        " ORDER BY id DESC LIMIT 1",
        (company_id, title, jd_raw, project_json, hard_struct_json),
    ).fetchone()
    if duplicate:
        return {"id": duplicate["id"], "company_id": company_id, "deduped": True}
    cur = conn.execute(
        "INSERT INTO positions (company_id, source, seq_no, title, jd_raw, consultant_notes,"
        " salary_range, hard_requirements, nice_to_have, headcount, project_status,"
        " consultant, is_project, hard_struct_json, project_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (company_id, "manual", body.seq_no, title, jd_raw, body.consultant_notes,
         body.salary_range, body.hard_requirements, body.nice_to_have,
         max(1, body.headcount), project_status, body.consultant, 1 if body.is_project else 0,
         hard_struct_json,
         project_json),
    )
    try:
        _auto_position_prompt(conn, cur.lastrowid, body.hard_requirements, body.nice_to_have, hard_struct)
        if company_id:
            feishu_store.push_company(conn, company_id, commit_record_id=False)
        feishu_store.push_position(conn, cur.lastrowid, commit_record_id=False)
        conn.commit()
        return {"id": cur.lastrowid, "company_id": company_id}
    except Exception:
        conn.rollback()
        raise


@router.post("/parse")
def parse_position_text(body: PositionParseIn, _user=Depends(require_position_operator)):
    text = body.text.strip()
    if len(text) < 20:
        raise HTTPException(422, "请先填写较完整的职位 JD 原文")
    items = _extract_positions_from_handbook_text(text, "手动粘贴JD")
    tokens = 0
    if not items:
        try:
            parsed, _raw, tokens, _latency = llm.chat_json(
                POSITION_IMPORT_SYSTEM,
                f"文件名：手动粘贴JD\n\n文件正文：\n{text[:60000]}",
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"DeepSeek 解析岗位 JD 失败（{type(e).__name__}: {e}）") from e
        items = parsed.get("positions") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise HTTPException(422, "未能从 JD 原文解析出岗位信息")
    item, warnings = _normalize_parsed_position(items[0])
    if not item["jd_raw"]:
        item["jd_raw"] = text
    return {
        "position": item,
        "count": len(items),
        "tokens": tokens,
        "unstructured_summary": item.get("unstructured_summary") or "",
        "warnings": warnings,
    }


@router.post("/upload")
async def upload_positions(file: UploadFile = File(...), _user=Depends(require_position_operator)):
    """上传本地岗位文件（PDF/DOCX/XLSX/TXT）→ 解析 → 自动新增岗位。"""
    filename = _safe_upload_display_name(file.filename or "未命名文件")
    suffix = Path(filename).suffix.lower()
    if suffix == ".xlsx":
        raw = await file.read()
        if not raw:
            raise HTTPException(422, f"{filename}：文件为空")
        dest = _position_xlsx_temp_path(document_importer.settings.upload_dir)
        dest.write_bytes(raw)
        try:
            items = _extract_positions_from_xlsx(dest)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"{filename}：Excel 解析失败（{type(e).__name__}: {e}）") from e
        finally:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
    elif suffix in (".xls", ".xlsm"):
        raise HTTPException(422, f"{filename}：岗位 Excel 导入仅支持 .xlsx，请另存为 .xlsx 后重新上传")
    else:
        try:
            text, filename = await document_importer.extract_upload_text(file, min_chars=50)
        except document_importer.DocumentImportError as e:
            raise HTTPException(422, str(e)) from e
        items = _extract_positions_from_handbook_text(text, filename)
        if not items:
            try:
                parsed, _raw, _tokens, _latency = llm.chat_json(
                    POSITION_IMPORT_SYSTEM,
                    f"文件名：{filename}\n\n文件正文：\n{text[:60000]}",
                )
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"{filename}：DeepSeek 解析岗位文件失败（{type(e).__name__}: {e}）") from e
            items = parsed.get("positions") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or not items:
        raise HTTPException(422, f"{filename}：未能解析出岗位，请检查文件是否包含岗位 JD/要求")

    conn = get_conn()
    created, skipped, errors = [], [], []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {index} 条：解析结果不是对象，已跳过")
            continue
        raw_title = str(item.get("title") or "").strip()
        company = str(item.get("company") or "").strip() or _company_from_title(raw_title)
        title = _strip_company_from_title(raw_title, company)
        jd_raw = str(item.get("jd_raw") or "").strip()
        if not title:
            errors.append(f"第 {index} 条：缺少岗位名称，已跳过")
            continue
        if not jd_raw:
            errors.append(f"岗位「{title}」：缺少 JD/岗位要求原文，已跳过")
            continue
        project_status = _normalize_project_status(item.get("project_status"))
        if project_status not in PROJECT_STATUS:
            errors.append(f"岗位「{title}」：项目状态「{project_status}」非法，已按未开始导入")
            project_status = "未开始"
        project = {
            key: str((item.get("project") or {}).get(key) or "").strip()
            for key in PROJECT_KEYS
        }
        hard_struct = _clean_struct(item.get("hard_struct") or {})
        try:
            conn.execute("SAVEPOINT position_import_item")
            _validate_project_dates(project)
            company_id = ensure_company(conn, company)
            if company_id:
                feishu_store.push_company(conn, company_id, commit_record_id=False)
            existing = _existing_position_for_import(conn, title, company_id)
            if existing:
                if company_id and not existing["company_id"]:
                    conn.execute(
                        "UPDATE positions SET company_id=?, updated_at=datetime('now','localtime') WHERE id=?",
                        (company_id, existing["id"]),
                    )
                    feishu_store.push_position(conn, existing["id"], commit_record_id=False)
                    skipped.append({"id": existing["id"], "title": title, "reason": "岗位已存在，已补全客户公司"})
                else:
                    skipped.append({"id": existing["id"], "title": title, "reason": "岗位已存在，无需重复导入"})
                conn.execute("RELEASE SAVEPOINT position_import_item")
                continue
            cur = conn.execute(
                "INSERT INTO positions (company_id, source, seq_no, title, jd_raw, consultant_notes,"
                " salary_range, hard_requirements, nice_to_have, headcount, project_status,"
                " hard_struct_json, project_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    company_id,
                    "manual",
                    str(item.get("seq_no") or "").strip(),
                    title,
                    jd_raw,
                    str(item.get("consultant_notes") or "").strip(),
                    str(item.get("salary_range") or "").strip(),
                    str(item.get("hard_requirements") or "").strip(),
                    str(item.get("nice_to_have") or "").strip(),
                    max(1, int(item.get("headcount") or 1)),
                    project_status,
                    json.dumps(hard_struct, ensure_ascii=False),
                    json.dumps(project, ensure_ascii=False),
                ),
            )
            _auto_position_prompt(
                conn,
                cur.lastrowid,
                str(item.get("hard_requirements") or "").strip(),
                str(item.get("nice_to_have") or "").strip(),
                hard_struct,
            )
            feishu_store.push_position(conn, cur.lastrowid, commit_record_id=False)
            created.append({"id": cur.lastrowid, "title": title})
            conn.execute("RELEASE SAVEPOINT position_import_item")
        except Exception as e:  # noqa: BLE001
            conn.execute("ROLLBACK TO SAVEPOINT position_import_item")
            conn.execute("RELEASE SAVEPOINT position_import_item")
            errors.append(f"岗位「{title}」入库失败（{type(e).__name__}: {e}）")
    if not created:
        conn.rollback()
        if skipped:
            return {"count": 0, "created": [], "skipped": skipped, "errors": errors}
        raise HTTPException(422, f"{filename}：没有岗位成功导入；" + "；".join(errors))
    conn.commit()
    return {"count": len(created), "created": created, "skipped": skipped, "errors": errors}


@router.put("/{position_id}")
def update_position(position_id: int, body: PositionUpdate, _user=Depends(require_position_operator)):
    conn = get_conn()
    row = conn.execute("SELECT id FROM positions WHERE id=?", (position_id,)).fetchone()
    if not row:
        raise HTTPException(404, "岗位不存在")
    if body.source is not None and body.source != "manual":
        raise HTTPException(400, "source 只允许 manual")
    if body.headcount is not None and body.headcount < 1:
        raise HTTPException(400, "招聘数量至少为 1")
    if body.project_status is not None and _normalize_project_status(body.project_status) not in PROJECT_STATUS:
        raise HTTPException(400, f"project_status 取值必须是 {PROJECT_STATUS}")
    fields, params = [], []
    for key in ("title", "jd_raw", "consultant_notes", "status", "source", "headcount",
                "salary_range", "hard_requirements", "nice_to_have",
                "project_status", "n_talents", "n_recommend", "n_interview", "n_offer",
                "consultant", "is_project"):
        val = getattr(body, key)
        if val is not None:
            if key == "source":
                val = "manual"
            elif key == "project_status":
                val = _normalize_project_status(val)
            fields.append(f"{key}=?")
            params.append(val)
    if body.project is not None:
        # 合并更新 project_json（只覆盖传入的键）
        old = conn.execute("SELECT project_json FROM positions WHERE id=?", (position_id,)).fetchone()
        merged = loads_or(old["project_json"], {})
        merged.update({k: str(v).strip() for k, v in body.project.items() if k in PROJECT_KEYS})
        _validate_project_dates(merged)
        fields.append("project_json=?")
        params.append(json.dumps(merged, ensure_ascii=False))
    if body.hard_struct is not None:
        fields.append("hard_struct_json=?")
        params.append(json.dumps(_clean_struct(body.hard_struct), ensure_ascii=False))
    company_id = None
    if "company_id" in body.model_fields_set:
        if body.company_id is not None:
            exists = conn.execute("SELECT id FROM companies WHERE id=?", (body.company_id,)).fetchone()
            if not exists:
                raise HTTPException(400, "客户不存在")
        company_id = body.company_id
        fields.append("company_id=?")
        params.append(company_id)
    elif body.company is not None:
        company_id = ensure_company(conn, body.company)
        fields.append("company_id=?")
        params.append(company_id)
    if not fields:
        return {"updated": False}
    params.append(position_id)
    try:
        conn.execute(
            f"UPDATE positions SET {', '.join(fields)}, updated_at=datetime('now','localtime') WHERE id=?",
            params,
        )
        if body.hard_requirements is not None or body.nice_to_have is not None or body.hard_struct is not None:
            row = conn.execute(
                "SELECT hard_requirements, nice_to_have, hard_struct_json FROM positions WHERE id=?", (position_id,)
            ).fetchone()
            _auto_position_prompt(
                conn,
                position_id,
                row["hard_requirements"],
                row["nice_to_have"],
                loads_or(row["hard_struct_json"], {}),
            )
        if company_id:
            feishu_store.push_company(conn, company_id, commit_record_id=False)
        feishu_store.push_position(conn, position_id, commit_record_id=False)
        conn.commit()
        return {"updated": True}
    except Exception:
        conn.rollback()
        raise


@router.delete("/{position_id}")
def delete_position(position_id: int, _user=Depends(require_position_operator)):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, title, feishu_record_id, feishu_project_record_id FROM positions WHERE id=?",
        (position_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "岗位不存在")
    try:
        tracking_ids = [
            item["id"] for item in conn.execute(
                "SELECT id FROM candidate_tracking WHERE position_id=?", (position_id,)
            ).fetchall()
        ]
        if tracking_ids:
            placeholders = ",".join(["?"] * len(tracking_ids))
            conn.execute(
                f"DELETE FROM tracking_logs WHERE tracking_id IN ({placeholders})",
                tracking_ids,
            )
        result_ids = [
            item["id"] for item in conn.execute(
                "SELECT id FROM match_results WHERE position_id=?",
                (position_id,),
            ).fetchall()
        ]
        for result_id in result_ids:
            match_result_cleanup.delete_match_result(conn, result_id)
        operation_log.record(
            conn,
            _user,
            "delete",
            "position",
            position_id,
            row["title"] or "",
            {"tracking_deleted": len(tracking_ids), "match_results_deleted": len(result_ids)},
        )
        conn.execute("DELETE FROM candidate_tracking WHERE position_id=?", (position_id,))
        conn.execute("DELETE FROM prompts WHERE position_id=?", (position_id,))
        conn.execute("DELETE FROM positions WHERE id=?", (position_id,))
        feishu_store.delete_position(row["feishu_record_id"])
        feishu_store.delete_project(row["feishu_project_record_id"])
        conn.commit()
        return {"deleted": True}
    except RuntimeError as exc:
        conn.rollback()
        raise HTTPException(502, str(exc)) from exc
    except Exception:
        conn.rollback()
        raise
