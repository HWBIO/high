"""岗位能力标签词典：从飞书 Base 读取，并为单个岗位筛选相关能力片段。"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from ..config import settings
from . import feishu

FIELD_NAMES = ("岗位族Label", "下设能力", "能力说明", "关键词/同义词", "正向证据", "反向边界")
CACHE_TTL_SECONDS = 300
MAX_LABEL_ROWS = 24

FAMILY_HINTS = {
    "技术研发": (
        "研发", "工程师", "后端", "服务端", "前端", "java", "go", "python", "c++", "runtime",
        "wasm", "webassembly", "架构", "引擎", "编辑器", "算法", "测试开发", "安全", "运维",
        "sre", "devops", "k8s", "kubernetes", "ai", "agent", "llm",
    ),
    "产品": (
        "产品经理", "产品专家", "产品总监", "产品负责人", "平台产品", "ai产品", "增长产品",
        "广告产品", "商业化产品", "数据产品", "开发者工具产品", "ide产品",
    ),
    "运营": (
        "运营", "增长运营", "内容运营", "用户运营", "社区运营", "广告变现", "游戏发行",
        "游戏运营", "渠道运营", "开发者生态", "海外运营",
    ),
    "销售/商务": (
        "销售", "商务", "bd", "客户", "ka", "大客户", "渠道销售", "海外销售", "出海商务",
        "售前", "解决方案", "客户成功", "游戏引入", "联运", "政企",
    ),
    "设计体验": (
        "设计", "体验", "ui", "ux", "交互", "视觉", "品牌", "设计系统", "用研", "aigc设计",
        "动效", "视频设计",
    ),
    "职能": (
        "hr", "hrbp", "招聘", "组织发展", "od", "薪酬", "绩效", "法务", "合规", "政府事务",
        "公共关系", "公关", "战略", "经营分析", "pmo", "行政", "采购",
    ),
    "金融": (
        "金融", "投研", "投资", "量化", "交易", "风控", "信贷", "授信", "资管", "基金",
        "支付", "清结算", "反洗钱", "kyc",
    ),
    "财务": (
        "财务", "fp&a", "财务bp", "核算", "总账", "应收", "应付", "出纳", "税务",
        "资金", "审计", "内控", "成本", "ipo", "上市财务",
    ),
}


@dataclass(frozen=True)
class JobLabelRow:
    family: str
    capability: str
    description: str
    synonyms: str
    positive_evidence: str
    negative_boundary: str


_cache_lock = threading.Lock()
_cache_expires_at = 0.0
_cache_rows: list[JobLabelRow] = []


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text_arr = item.get("text_arr")
                if isinstance(text_arr, list):
                    parts.append("".join(str(part.get("text") if isinstance(part, dict) else part) for part in text_arr))
                else:
                    parts.append(str(item.get("text") or item.get("name") or item.get("id") or ""))
            else:
                parts.append(str(item))
        return "、".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("id") or json.dumps(value, ensure_ascii=False))
    return str(value).strip()


def _row_from_fields(fields: dict[str, Any]) -> Optional[JobLabelRow]:
    row = JobLabelRow(
        family=_value(fields.get("岗位族Label")),
        capability=_value(fields.get("下设能力")),
        description=_value(fields.get("能力说明")),
        synonyms=_value(fields.get("关键词/同义词")),
        positive_evidence=_value(fields.get("正向证据")),
        negative_boundary=_value(fields.get("反向边界")),
    )
    if not row.family or not row.capability:
        return None
    return row


def _fetch_rows_from_feishu() -> list[JobLabelRow]:
    if not settings.feishu_table_job_labels or not settings.feishu_base_token:
        return []
    rows: list[JobLabelRow] = []
    page_token = ""
    while True:
        data = feishu.client.list_records(
            settings.feishu_table_job_labels,
            page_size=200,
            page_token=page_token,
        )
        for item in data.get("items") or []:
            row = _row_from_fields(item.get("fields") or {})
            if row:
                rows.append(row)
        if not data.get("has_more"):
            return rows
        page_token = data.get("page_token") or ""
        if not page_token:
            return rows


def clear_cache() -> None:
    global _cache_expires_at, _cache_rows
    with _cache_lock:
        _cache_expires_at = 0.0
        _cache_rows = []


def label_rows() -> list[JobLabelRow]:
    global _cache_expires_at, _cache_rows
    now = time.time()
    with _cache_lock:
        if _cache_rows and now < _cache_expires_at:
            return list(_cache_rows)
    try:
        rows = _fetch_rows_from_feishu()
    except Exception:  # noqa: BLE001 词典读取失败不能阻断匹配主流程
        rows = []
    with _cache_lock:
        _cache_rows = rows
        _cache_expires_at = now + CACHE_TTL_SECONDS
    return list(rows)


def _position_text(position: dict, company_prompt: str, position_prompt: str) -> str:
    struct = position.get("jd_struct") or {}
    parts = [
        position.get("title") or "",
        position.get("company_name") or "",
        position.get("jd_raw") or "",
        position.get("hard_requirements") or "",
        position.get("nice_to_have") or "",
        position.get("consultant_notes") or "",
        company_prompt or "",
        position_prompt or "",
    ]
    for value in struct.values():
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.append(json.dumps(value, ensure_ascii=False))
        elif value:
            parts.append(str(value))
    return "\n".join(parts)


def _contains(text: str, term: str) -> bool:
    term = term.strip()
    if not term:
        return False
    return term.lower() in text.lower()


def _split_terms(text: str) -> list[str]:
    terms = re.split(r"[,，、/｜|;\n\r]+", text or "")
    return [term.strip() for term in terms if len(term.strip()) >= 2]


def _family_scores(rows: list[JobLabelRow], source: str) -> dict[str, int]:
    scores = {row.family: 0 for row in rows}
    lowered = source.lower()
    for family, hints in FAMILY_HINTS.items():
        for hint in hints:
            if hint.lower() in lowered:
                scores[family] = scores.get(family, 0) + 12
    for row in rows:
        if _contains(source, row.family):
            scores[row.family] = scores.get(row.family, 0) + 30
        if _contains(source, row.capability):
            scores[row.family] = scores.get(row.family, 0) + 18
        for term in _split_terms(row.synonyms):
            if _contains(source, term):
                scores[row.family] = scores.get(row.family, 0) + 4
    return scores


def _selected_families(rows: list[JobLabelRow], source: str) -> list[str]:
    scores = _family_scores(rows, source)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return []
    best = ranked[0][1]
    return [family for family, score in ranked[:3] if score > 0 and score >= best * 0.55]


def _row_score(row: JobLabelRow, source: str) -> int:
    score = 0
    if _contains(source, row.capability):
        score += 30
    for term in _split_terms(row.synonyms):
        if _contains(source, term):
            score += 8
    for term in _split_terms(row.description):
        if _contains(source, term):
            score += 2
    return score


def relevant_label_rows(
    position: dict,
    company_prompt: str = "",
    position_prompt: str = "",
    rows: Optional[list[JobLabelRow]] = None,
) -> tuple[list[JobLabelRow], list[str]]:
    primary_source = _position_text(position, "", position_prompt)
    source = _position_text(position, company_prompt, position_prompt)
    all_rows = rows if rows is not None else label_rows()
    if not source.strip() or not all_rows:
        return [], []
    families = _selected_families(all_rows, primary_source) or _selected_families(all_rows, source)
    if not families:
        return [], []
    family_rows = [row for row in all_rows if row.family in families]
    title_source = str(position.get("title") or "")
    scored = [
        (row, _row_score(row, title_source) * 4 + _row_score(row, primary_source) * 2 + _row_score(row, source))
        for row in family_rows
    ]
    positive = [(row, score) for row, score in scored if score > 0]
    selected = positive or scored
    selected = sorted(selected, key=lambda item: (item[1], item[0].family == families[0]), reverse=True)
    return [row for row, _score in selected[:MAX_LABEL_ROWS]], families


def compose_label_block(position: dict, company_prompt: str = "", position_prompt: str = "") -> str:
    rows, families = relevant_label_rows(position, company_prompt, position_prompt)
    if not rows:
        return ""
    lines = [
        "## 岗位能力标签词典片段（系统按本岗位自动筛选）",
        "用途：辅助判断岗位族、下设能力、正向证据与反向边界。",
        f"候选岗位族Label候选：{'、'.join(families)}",
    ]
    for idx, row in enumerate(rows, start=1):
        lines.extend(
            [
                f"{idx}. 岗位族Label：{row.family}；下设能力：{row.capability}",
                f"   能力说明：{row.description}",
                f"   关键词/同义词：{row.synonyms}",
                f"   正向证据：{row.positive_evidence}",
                f"   反向边界：{row.negative_boundary}",
            ]
        )
    return "\n".join(lines)
