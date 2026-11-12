import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import matcher  # noqa: E402


def _row(requirement, condition_type="blocker", label="待确认", note=""):
    return {
        "requirement": requirement,
        "condition_type": condition_type,
        "label": label,
        "note": note,
    }


def test_postprocess_demotes_age_inferred_from_education():
    rows = matcher._postprocess_detail_rows(
        [_row("32岁以下", note="候选人2015年入学本科，推测年龄约28-30岁，需顾问确认。")],
        "候选人主线一致。",
        {"title": "高级Java研发工程师"},
    )

    age_row = next(row for row in rows if row["requirement"] == "32岁以下")
    assert age_row["condition_type"] == "risk"
    assert age_row["label"] == "待确认"
    assert "不能从入学/毕业年份反推年龄" in age_row["note"]


def test_postprocess_demotes_stability_without_explicit_veto():
    rows = matcher._postprocess_detail_rows(
        [_row("跳槽频率（过往任职每家公司服务年限≥3年）", note="任职时间较短，需确认稳定性。")],
        "候选人主线一致。",
        {"title": "高级Java研发工程师"},
    )

    stability_row = next(row for row in rows if row["requirement"].startswith("跳槽频率"))
    assert stability_row["condition_type"] == "risk"


def test_postprocess_keeps_explicit_stability_veto_as_blocker():
    rows = matcher._postprocess_detail_rows(
        [_row("客户一票否决：不接受频繁跳槽", note="客户明确不接受频繁跳槽。")],
        "候选人主线一致。",
        {"title": "高级Java研发工程师"},
    )

    stability_row = next(row for row in rows if row["requirement"].startswith("客户一票否决"))
    assert stability_row["condition_type"] == "blocker"


def test_postprocess_demotes_java_extra_preferences():
    rows = matcher._postprocess_detail_rows(
        [_row("具备 Node/Python 或 AI Agent 经验", condition_type="core", label="不满足", note="简历未提及。")],
        "候选人为Java后端主线。",
        {"title": "高级Java研发工程师"},
    )

    extra_row = next(row for row in rows if row["requirement"].startswith("具备 Node"))
    assert extra_row["condition_type"] == "preference"


def test_postprocess_adds_mainline_mismatch_row_from_analysis():
    rows = matcher._postprocess_detail_rows(
        [_row("熟悉React", condition_type="core", label="不满足", note="未提及。")],
        "候选人是后端/架构主线，和前端岗位主线明显不一致。",
        {"title": "高级前端研发工程师"},
    )

    assert rows[0]["requirement"] == "岗位主线一致性"
    assert rows[0]["condition_type"] == "blocker"
    assert rows[0]["label"] == "不满足"
