"""匹配评分规则。

模型负责拆条件和标注证据；系统负责把条件转成可解释分数。
最终分不再使用“满足条数 / 总条数”的简单命中率，而是：
- blocker 只做关键门槛检查和封顶；
- core/relevance/seniority/preference/risk 五个维度加权；
- 待确认给较高的初筛信用，同时降低置信度。
"""
from typing import Optional

ALLOWED_LABELS = {"满足", "待确认", "不满足"}
LABEL_ALIASES = {"完全匹配": "满足", "未提及/待确认": "待确认", "不匹配": "不满足"}

CONDITION_TYPES = ("blocker", "core", "relevance", "seniority", "preference", "risk")
CONDITION_TYPE_ALIASES = {
    "hard": "core",
    "must": "blocker",
    "priority": "preference",
    "soft": "preference",
}

DIMENSION_WEIGHTS = {
    "core": 0.40,
    "relevance": 0.25,
    "seniority": 0.15,
    "preference": 0.10,
    "risk": 0.10,
}

LABEL_VALUES = {
    "满足": 1.0,
    "待确认": 0.75,
    "不满足": 0.0,
}

CONDITION_LABELS = {
    "blocker": "关键门槛",
    "core": "核心能力",
    "relevance": "相关经验",
    "seniority": "资历/复杂度",
    "preference": "客户偏好",
    "risk": "风险可控度",
}

SCORING_POLICY_VERSION = "dimension_weighted_with_mainline_cap_v6"


def normalize_label(label: str) -> str:
    normalized = LABEL_ALIASES.get(label, label)
    return normalized if normalized in ALLOWED_LABELS else "待确认"


def normalize_condition_type(condition_type: str) -> str:
    normalized = CONDITION_TYPE_ALIASES.get(condition_type, condition_type)
    return normalized if normalized in CONDITION_TYPES else "preference"


def count_rows(rows: list) -> dict:
    total = len(rows)
    satisfied = sum(1 for row in rows if normalize_label(row.get("label")) == "满足")
    failed = sum(1 for row in rows if normalize_label(row.get("label")) == "不满足")
    pending = sum(1 for row in rows if normalize_label(row.get("label")) == "待确认")
    raw_score = round((satisfied / total) * 100, 1) if total else 0
    adjusted_score = round(
        (sum(LABEL_VALUES[normalize_label(row.get("label"))] for row in rows) / total) * 100,
        1,
    ) if total else 0
    return {
        "total": total,
        "satisfied": satisfied,
        "failed": failed,
        "pending": pending,
        "score": adjusted_score,
        "raw_score": raw_score,
    }


def _dimension_rows(rows: list, condition_type: str) -> list:
    return [row for row in rows if normalize_condition_type(row.get("condition_type")) == condition_type]


def _weighted_score(dimensions: dict) -> float:
    present = {
        key: dimensions[key]
        for key in DIMENSION_WEIGHTS
        if dimensions.get(key, {}).get("total", 0) > 0
    }
    if not present:
        return 0
    weight_total = sum(DIMENSION_WEIGHTS[key] for key in present)
    return round(
        sum(present[key]["score"] * DIMENSION_WEIGHTS[key] for key in present) / weight_total,
        1,
    )


def _confidence(all_counts: dict) -> int:
    total = all_counts.get("total", 0)
    if not total:
        return 0
    pending = all_counts.get("pending", 0)
    failed = all_counts.get("failed", 0)
    return max(0, min(100, round(100 - pending / total * 30 - failed / total * 50)))


def _apply_blocker_cap(score: float, blocker: dict) -> tuple:
    if blocker.get("failed", 0) > 0:
        return min(score, 59), "关键门槛明确不满足，暂不建议推荐"
    if blocker.get("pending", 0) > 0:
        return min(score, 88), "可进入人工审核，需优先确认关键门槛"
    return score, None


def _mainline_label(rows: list) -> Optional[str]:
    for row in rows:
        requirement = str(row.get("requirement") or "")
        if "岗位主线" in requirement and "一致" in requirement:
            return normalize_label(row.get("label"))
    return None


def _apply_mainline_cap(score: float, rows: list) -> tuple:
    label = _mainline_label(rows)
    if label == "不满足":
        return min(score, 59), "岗位主线明显不一致，暂不建议推荐"
    if label == "待确认":
        return min(score, 75), "岗位主线相邻但不完全一致，需人工确认后再推荐"
    return score, None


def compose_condition_scores(rows: list, review_threshold: int = 80, top_threshold: int = 90) -> dict:
    normalized_rows = [
        {**row, "condition_type": normalize_condition_type(row.get("condition_type")), "label": normalize_label(row.get("label"))}
        for row in rows
    ]
    dimensions = {
        key: {**count_rows(_dimension_rows(normalized_rows, key)), "label": CONDITION_LABELS[key], "weight": DIMENSION_WEIGHTS.get(key, 0)}
        for key in CONDITION_TYPES
    }
    blocker = dimensions["blocker"]
    all_counts = count_rows(normalized_rows)
    weighted = _weighted_score(dimensions)
    final_score, mainline_judgement = _apply_mainline_cap(weighted, normalized_rows)
    final_score, blocker_judgement = _apply_blocker_cap(final_score, blocker)
    cap_judgement = blocker_judgement if blocker.get("failed", 0) else (mainline_judgement or blocker_judgement)
    final_score = round(final_score, 1)
    confidence = _confidence(all_counts)

    if cap_judgement:
        judgement = cap_judgement
        if mainline_judgement or blocker.get("failed", 0):
            level = "caution"
        else:
            level = "review" if final_score >= review_threshold else None
    elif final_score >= top_threshold:
        judgement = "建议优先推荐客户"
        level = "top"
    elif final_score >= review_threshold:
        judgement = "可进入人工审核"
        level = "review"
    elif final_score >= 70:
        judgement = "有匹配基础，建议补充确认后再判断"
        level = None
    else:
        judgement = "匹配基础较弱，需结合客户接受度判断"
        level = None

    hard_rows = [row for row in normalized_rows if row["condition_type"] in ("blocker", "core")]
    priority_rows = [row for row in normalized_rows if row["condition_type"] in ("relevance", "seniority", "preference", "risk")]

    return {
        "blocker": {**blocker, "passed": blocker.get("failed", 0) == 0},
        "core": dimensions["core"],
        "relevance": dimensions["relevance"],
        "seniority": dimensions["seniority"],
        "preference": dimensions["preference"],
        "risk": dimensions["risk"],
        "dimensions": [
            dimensions[key]
            for key in ("core", "relevance", "seniority", "preference", "risk")
            if dimensions[key]["total"] > 0
        ],
        # 兼容旧前端/导出：hard=关键门槛+核心能力，priority=其余维度。
        "hard": {**count_rows(hard_rows), "passed": blocker.get("failed", 0) == 0},
        "priority": count_rows(priority_rows),
        "all": all_counts,
        "confidence": confidence,
        "scoring_rule": (
            "拆条目用于解释，不直接按条数命中率给最终分；关键门槛只做风险和封顶。"
            "最终分按核心能力40%、相关经验25%、资历/复杂度15%、客户偏好10%、风险可控度10%加权；"
            "待确认给75%初筛信用并降低置信度；关键门槛明确不满足时最高59分，关键门槛待确认时最高88分。"
            "岗位主线明显不一致时最高59分，相邻岗位族最高75分。"
        ),
        "final_score": final_score,
        "judgement": judgement,
        "recommend_level": level,
    }


def rule_info(review_threshold: int = 80, top_threshold: int = 90) -> dict:
    return {
        "weights": {key: int(value * 100) for key, value in DIMENSION_WEIGHTS.items()},
        "thresholds": {"review": review_threshold, "top": top_threshold},
        "condition_types": [
            {"key": "blocker", "label": "关键门槛", "desc": "真正一票否决或强封顶项，例如必须城市、必须学历、不可接受外包、明确薪资超预算。"},
            {"key": "core", "label": "核心能力", "desc": "岗位最核心的技能、职责和产出能力。"},
            {"key": "relevance", "label": "相关经验", "desc": "行业、产品形态、业务场景、技术栈相似度。"},
            {"key": "seniority", "label": "资历/复杂度", "desc": "年限、项目规模、架构复杂度、管理范围、0-1或攻坚经验。"},
            {"key": "preference", "label": "客户偏好", "desc": "客户偏好的背景、公司类型、工具链、额外加分项。"},
            {"key": "risk", "label": "风险可控度", "desc": "薪资、地点、稳定性、到岗、简历缺失等推进风险。"},
        ],
        "labels": ["满足", "不满足", "待确认"],
        "scoring_rule": (
            "条目用于解释；最终分按维度加权。满足=100%信用，待确认=75%初筛信用，不满足=0；"
            "待确认同时降低置信度。关键门槛失败最高59分，关键门槛待确认最高88分。"
            "岗位主线明显不一致最高59分，相邻岗位族最高75分。"
        ),
    }
