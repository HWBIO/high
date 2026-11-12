"""简历 PDF 解析：pdfplumber 提取文本 + 中文简历分节。

分节逻辑移植自 playground/build_resume_comparison.py（SECTION_ALIASES / split_sections）。
"""
import re
from pathlib import Path

SECTION_ALIASES = [
    ("个人信息", ["个人信息", "基本信息"]),
    ("求职方向", ["求职方向", "求职意向", "应聘岗位", "目标岗位"]),
    ("教育背景", ["教育背景", "教育经历", "教育"]),
    ("工作经历", ["工作经历", "工作经验"]),
    ("实习经历", ["实习经历"]),
    ("项目经历", ["项目经历", "项目经验", "项目"]),
    ("校园经历", ["校园经历", "社团经历", "学生工作"]),
    ("技能证书", ["技能证书", "专业技能", "技能", "证书", "语言能力", "掌握技能"]),
    ("荣誉奖项", ["荣誉奖项", "获奖经历", "奖项", "荣誉"]),
    ("自我评价", ["自我评价", "个人评价", "个人优势"]),
]

MIN_TEXT_CHARS = 200  # 低于此值判定为疑似扫描件
NAME_STOPWORDS = {
    "个人简历", "个人信息", "基本信息", "教育背景", "教育经历", "工作经历", "工作经验",
    "项目经历", "项目经验", "专业技能", "技能证书", "求职方向", "求职意向",
    "游戏制作", "运维负责人", "解决方案", "架构师", "高级前端", "高级后端",
    "文字简历", "粘贴简历",
}
CITY_WORDS = {
    "北京", "上海", "深圳", "广州", "杭州", "成都", "南京", "武汉", "厦门", "苏州",
    "西安", "长沙", "重庆", "天津", "青岛", "合肥", "郑州",
}
ROLE_HINTS = (
    "工程师", "负责人", "负责", "架构", "产品", "运营", "运维", "销售", "商务", "设计", "开发",
    "经理", "总监", "专家", "顾问", "引擎", "服务端", "前端", "后端", "简历",
    "背景", "经历", "岗位", "个人", "教育",
)


class ScannedPDFError(Exception):
    """上传文件无法提取出可用于匹配的简历文字。"""


def normalize_text(text: str) -> str:
    text = text.replace("\r", "\n").replace(" ", "\n")
    text = text.replace("", "•")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def canonical_section(line: str):
    cleaned = re.sub(r"[:：\s]+$", "", line.strip())
    cleaned = cleaned.lstrip("•- ").strip()
    if len(cleaned) > 12:
        return None
    for title, aliases in SECTION_ALIASES:
        if cleaned in aliases:
            return title
    return None


def split_sections(text: str):
    """按分节标题切分，返回 [(标题, [行, ...]), ...]；无标题的开头归入「原始内容」。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    sections = []
    current_title = "原始内容"
    current_lines = []
    for line in lines:
        title = canonical_section(line)
        if title:
            if current_lines:
                sections.append((current_title, current_lines))
            current_title = title
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, current_lines))
    return sections


def extract_pdf_text(path: Path) -> str:
    import pdfplumber

    parts = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return normalize_text("\n".join(parts))


def extract_docx_text(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return normalize_text("\n".join(parts))


def extract_txt_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return normalize_text(path.read_text(encoding=encoding))
        except UnicodeDecodeError:
            continue
    return normalize_text(path.read_text(errors="ignore"))


def _looks_like_person_name(value: str) -> bool:
    value = value.strip()
    if not re.fullmatch(r"[一-鿿]{2,4}", value):
        return False
    if value in NAME_STOPWORDS or value in CITY_WORDS:
        return False
    return not any(hint in value for hint in ROLE_HINTS)


def _name_from_filename(filename: str) -> str:
    stem = Path(filename).stem if filename else ""
    stem = re.sub(r"【[^】]*】", "", stem)  # 去掉【岗位/标签】前缀
    stem = re.sub(r"\([^)]*\)|（[^）]*）", " ", stem)
    parts = [part.strip() for part in re.split(r"[-_+＋,，、\s]+", stem) if part.strip()]
    for part in parts:
        for name in re.findall(r"[一-鿿]{2,4}", part):
            if _looks_like_person_name(name):
                return name
    return ""


def guess_name(text: str, filename: str = "") -> str:
    """优先从文件名提取候选人姓名，失败再从正文首部猜测。"""
    filename_name = _name_from_filename(filename)
    if filename_name:
        return filename_name
    for line in text.splitlines()[:8]:
        line = line.strip()
        if _looks_like_person_name(line):
            return line
    stem = Path(filename).stem if filename else ""
    for name in re.findall(r"[一-鿿]{2,4}", stem):
        if _looks_like_person_name(name):
            return name
    if stem.startswith(("文字简历_", "粘贴简历_")):
        return "未知候选人"
    return stem or "未知候选人"


def parse_resume(path: Path, filename: str = "") -> dict:
    """返回 {name, text, sections: [{title, lines}], warnings: []}。"""
    return parse_resume_file(path, filename)


def parse_resume_file(path: Path, filename: str = "") -> dict:
    """解析上传简历。MVP 支持 PDF/DOCX/TXT。"""
    suffix = Path(filename or path.name).suffix.lower()
    if suffix == ".pdf":
        text = extract_pdf_text(path)
    elif suffix == ".docx":
        text = extract_docx_text(path)
    elif suffix in (".txt", ".md"):
        text = extract_txt_text(path)
    elif suffix == ".doc":
        raise ScannedPDFError("旧版 .doc 暂未接入转换服务，请先转为 .docx 或 PDF 后上传")
    elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
        raise ScannedPDFError("暂不支持图片简历，请上传文字版 PDF/DOCX/TXT 简历")
    else:
        raise ScannedPDFError("暂不支持该文件格式，请上传 PDF/DOCX/TXT 简历")
    if len(text) < MIN_TEXT_CHARS:
        raise ScannedPDFError(
            f"仅提取到 {len(text)} 个字符，疑似扫描件或图片型文件，暂不支持（请提供文字版简历）"
        )
    warnings = []
    sections = [
        {"title": title, "lines": lines} for title, lines in split_sections(text)
    ]
    known = {s["title"] for s in sections} - {"原始内容"}
    if not known:
        warnings.append("未识别出标准简历分节标题，将按原文整体处理")
    return {
        "name": guess_name(text, filename),
        "text": text,
        "sections": sections,
        "warnings": warnings,
    }


def sections_to_text(sections: list) -> str:
    """分节结果转回给 LLM 的排版文本。"""
    parts = []
    for sec in sections:
        parts.append(f"### {sec['title']}")
        parts.extend(sec["lines"])
        parts.append("")
    return "\n".join(parts).strip()
