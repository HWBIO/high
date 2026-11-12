import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import prompt_composer


def test_compose_system_contains_all_layers():
    system = prompt_composer.compose_system("我是通用提示词", "detail")
    assert "我是通用提示词" in system
    assert "明细阶段输出规则" in system
    assert "参考示例" in system  # few-shot 仅明细阶段


def _position(**overrides):
    pos = {
        "id": 1,
        "title": "AI 工程师",
        "company_name": "超线性",
        "company_id": 1,
        "jd_raw": "JD原文内容",
        "consultant_notes": "",
        "jd_struct": {"requirements": ["3年经验", "熟悉Python"]},
    }
    pos.update(overrides)
    return pos


def test_detail_user_layer_order():
    user = prompt_composer.compose_detail_user(
        _position(),
        "简历全文",
        company_prompt="公司偏好X",
        position_prompt="必须有云原生生产经验",
        label_context="## 岗位能力标签词典片段\nJava后端研发",
    )
    i_company = user.index("公司偏好X")
    i_position = user.index("必须有云原生生产经验")
    i_label = user.index("Java后端研发")
    i_jd = user.index("JD原文内容")
    i_resume = user.index("简历全文")
    assert i_company < i_position < i_label < i_jd < i_resume
    assert "岗位补充提示（先区分真正关键门槛与普通能力要求）" in user
    assert "1. 3年经验" in user  # requirements 编号列出


def test_detail_user_keeps_jd_raw_as_required_source_when_structured_requirements_exist():
    user = prompt_composer.compose_detail_user(
        _position(jd_raw="完整JD原文要求A；隐藏在原文里的要求B。"),
        "简历全文",
    )

    assert "JD结构化要求列表" in user
    assert "岗位JD原文底稿" in user
    assert "隐藏在原文里的要求B" in user


def test_detail_user_missing_layers_omitted():
    user = prompt_composer.compose_detail_user(_position(), "简历全文")
    assert "公司背景与偏好" not in user
    assert "岗位特殊规则" not in user


def test_detail_user_consultant_notes():
    user = prompt_composer.compose_detail_user(
        _position(consultant_notes="客户隐含要求稳定性"), "简历"
    )
    assert "顾问备注" in user
    assert "客户隐含要求稳定性" in user


def test_candidate_resume_block_prefers_raw_content_section_over_education_only():
    candidate = {
        "resume_struct_json": json.dumps(
            [
                {"title": "原始内容", "lines": ["王顺云", "主导We2000亿级用户系统，峰值TPS 1.3万，SLA 99.99%。"]},
                {"title": "教育背景", "lines": ["信息管理与信息系统 | 中国地质大学（武汉） | 本科"]},
            ],
            ensure_ascii=False,
        )
    }
    position = {"title": "服务端基础架构专家", "jd_struct": {"requirements": ["大型分布式高并发平台"]}}

    block = prompt_composer.candidate_resume_block(candidate, "", position, "", "")

    assert "主导We2000亿级用户系统" in block
    assert "教育背景" not in block
