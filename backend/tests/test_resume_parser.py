import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.services import resume_parser

SAMPLE = """张三
电话 138-0000-0000 | 邮箱 test@example.com

教育背景
2014.09 - 2018.06 某大学 计算机科学 本科

工作经历
2020.03 - 至今 某科技公司 算法工程师
• 主导内部 Agent 平台建设，实现记忆机制与工具编排
• 搭建基于向量库的知识问答系统（RAG）

专业技能
• Python、PyTorch、LangChain
"""


def test_split_sections():
    sections = resume_parser.split_sections(SAMPLE)
    titles = [t for t, _ in sections]
    assert "教育背景" in titles
    assert "工作经历" in titles
    assert "技能证书" in titles  # "专业技能" 归一化为技能证书
    work = dict(sections)["工作经历"]
    assert any("Agent 平台" in line for line in work)


def test_guess_name():
    assert resume_parser.guess_name(SAMPLE) == "张三"
    assert resume_parser.guess_name("no chinese", "【产品经理】李四_简历.pdf") == "李四"
    assert resume_parser.guess_name("游戏制作\n教育背景", "游戏制作人-陈超峰-深圳 上海(1).pdf") == "陈超峰"
    assert resume_parser.guess_name("个人简历\n工作经历", "解决方案架构师（SA）-侯宪斌-深圳.docx") == "侯宪斌"
    assert resume_parser.guess_name("教育背景\n腾讯云", "运维负责人-童琳-深圳.pdf") == "童琳"
    assert resume_parser.guess_name("个人简历\n工作经历", "文字简历_1_1780000000000.txt") == "未知候选人"


def test_sections_to_text_roundtrip():
    sections = [
        {"title": s[0], "lines": s[1]} for s in resume_parser.split_sections(SAMPLE)
    ]
    text = resume_parser.sections_to_text(sections)
    assert "### 教育背景" in text
    assert "RAG" in text


def test_scanned_pdf_error(monkeypatch, tmp_path):
    monkeypatch.setattr(resume_parser, "extract_pdf_text", lambda path: "太短的文本")
    fake = tmp_path / "scan.pdf"
    fake.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(resume_parser.ScannedPDFError):
        resume_parser.parse_resume(fake)
