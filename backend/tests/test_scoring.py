"""维度加权评分的纯函数测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import scoring  # noqa: E402


def test_pending_gets_partial_credit_not_zero():
    result = scoring.compose_condition_scores([
        {"condition_type": "core", "label": "满足"},
        {"condition_type": "core", "label": "待确认"},
    ])

    assert result["core"]["raw_score"] == 50
    assert result["core"]["score"] == 87.5
    assert result["final_score"] == 87.5
    assert result["recommend_level"] == "review"
    assert result["confidence"] == 85


def test_blocker_pending_caps_but_allows_review():
    result = scoring.compose_condition_scores([
        {"condition_type": "blocker", "label": "待确认"},
        {"condition_type": "core", "label": "满足"},
        {"condition_type": "relevance", "label": "满足"},
    ])

    assert result["blocker"]["pending"] == 1
    assert result["final_score"] == 88
    assert result["recommend_level"] == "review"
    assert result["judgement"] == "可进入人工审核，需优先确认关键门槛"


def test_blocker_failed_caps_at_59():
    result = scoring.compose_condition_scores([
        {"condition_type": "blocker", "label": "不满足"},
        {"condition_type": "core", "label": "满足"},
        {"condition_type": "relevance", "label": "满足"},
    ])

    assert result["blocker"]["passed"] is False
    assert result["final_score"] == 59
    assert result["recommend_level"] == "caution"


def test_legacy_types_are_mapped():
    result = scoring.compose_condition_scores([
        {"condition_type": "hard", "label": "完全匹配"},
        {"condition_type": "soft", "label": "未提及/待确认"},
        {"condition_type": "soft", "label": "不匹配"},
    ])

    assert result["core"]["score"] == 100
    assert result["preference"]["total"] == 2
    assert result["preference"]["pending"] == 1
    assert result["preference"]["failed"] == 1
    assert result["priority"]["total"] == 2


def test_top_threshold_uses_weighted_dimensions():
    result = scoring.compose_condition_scores([
        {"condition_type": "core", "label": "满足"},
        {"condition_type": "relevance", "label": "满足"},
        {"condition_type": "seniority", "label": "满足"},
        {"condition_type": "preference", "label": "待确认"},
        {"condition_type": "risk", "label": "满足"},
    ])

    assert result["final_score"] == 97.5
    assert result["recommend_level"] == "top"
    assert result["judgement"] == "建议优先推荐客户"


def test_mainline_mismatch_caps_at_59_even_with_strong_general_fit():
    result = scoring.compose_condition_scores([
        {"requirement": "岗位主线一致性", "condition_type": "core", "label": "不满足"},
        {"condition_type": "core", "label": "满足"},
        {"condition_type": "relevance", "label": "满足"},
        {"condition_type": "seniority", "label": "满足"},
        {"condition_type": "preference", "label": "满足"},
        {"condition_type": "risk", "label": "满足"},
    ])

    assert result["final_score"] == 59
    assert result["recommend_level"] == "caution"
    assert result["judgement"] == "岗位主线明显不一致，暂不建议推荐"


def test_adjacent_mainline_caps_at_75():
    result = scoring.compose_condition_scores([
        {"requirement": "岗位主线一致性", "condition_type": "core", "label": "待确认"},
        {"condition_type": "core", "label": "满足"},
        {"condition_type": "relevance", "label": "满足"},
        {"condition_type": "seniority", "label": "满足"},
        {"condition_type": "preference", "label": "满足"},
        {"condition_type": "risk", "label": "满足"},
    ])

    assert result["final_score"] == 75
    assert result["recommend_level"] == "caution"
    assert result["judgement"] == "岗位主线相邻但不完全一致，需人工确认后再推荐"
