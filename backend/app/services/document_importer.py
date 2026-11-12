"""本地文档导入：PDF/DOCX/XLSX/TXT -> 文本。

用于岗位管理、客户管理的本地文件解析。这里只负责抽文本，不负责业务结构化；
业务结构化统一交给 LLM 层，便于切换 DeepSeek/网关模型。
"""
import time
import uuid
from pathlib import Path

from fastapi import UploadFile
from openpyxl import load_workbook

from ..config import settings
from . import resume_parser


class DocumentImportError(Exception):
    """导入文档无法提取可用文本。"""


def _cell_text(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").strip()


def extract_xlsx_text(path: Path) -> str:
    workbook = load_workbook(path, read_only=True, data_only=True)
    parts = []
    try:
        for sheet in workbook.worksheets:
            rows = [
                [_cell_text(cell) for cell in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            rows = [row for row in rows if any(cell for cell in row)]
            if not rows:
                continue
            max_cols = max(len(row) for row in rows)
            parts.append(f"## Sheet: {sheet.title}")
            for index, row in enumerate(rows, start=1):
                padded = row + [""] * (max_cols - len(row))
                parts.append(f"Row {index}: " + " | ".join(padded))
            parts.append("")
    finally:
        workbook.close()
    return resume_parser.normalize_text("\n".join(parts))


async def extract_upload_text(file: UploadFile, min_chars: int = 20) -> tuple[str, str]:
    filename = file.filename or "未命名文件"
    suffix = Path(filename).suffix.lower()
    raw = await file.read()
    if not raw:
        raise DocumentImportError(f"{filename}：文件为空")
    dest = settings.upload_dir / f"import_{int(time.time())}_{uuid.uuid4().hex[:8]}{suffix or '.bin'}"
    dest.write_bytes(raw)
    try:
        if suffix == ".pdf":
            text = resume_parser.extract_pdf_text(dest)
        elif suffix == ".docx":
            text = resume_parser.extract_docx_text(dest)
        elif suffix == ".xlsx":
            text = extract_xlsx_text(dest)
        elif suffix in (".txt", ".md"):
            text = raw.decode("utf-8", errors="replace")
            text = resume_parser.normalize_text(text)
        elif suffix == ".doc":
            raise DocumentImportError(f"{filename}：旧版 .doc 暂未接入转换服务，请先转为 .docx 或 PDF")
        else:
            raise DocumentImportError(f"{filename}：暂不支持该格式，请上传 PDF/DOCX/XLSX/TXT")
        if len(text.strip()) < min_chars:
            raise DocumentImportError(f"{filename}：仅提取到 {len(text.strip())} 个字符，无法解析，请检查是否为扫描件/图片型文件")
        return text, filename
    finally:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
