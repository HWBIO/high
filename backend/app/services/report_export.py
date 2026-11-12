import re
import tempfile
from pathlib import Path

from ..config import settings

CONDITION_GROUPS = [
    ("blocker", "关键门槛", "风险/待确认点"),
    ("core", "核心能力", "风险/待确认点"),
    ("relevance", "相关经验", "亮点/缺口"),
    ("seniority", "资历/复杂度", "亮点/缺口"),
    ("preference", "客户偏好", "亮点/缺口"),
    ("risk", "风险可控度", "风险/待确认点"),
]
CONDITION_ALIASES = {"hard": "core", "priority": "preference", "soft": "preference"}


def safe_filename(name: str, suffix: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return f"{clean or 'match_report'}{suffix}"


def _title(report: dict) -> str:
    detail = report.get("details", [{}])[0] if report.get("details") else {}
    if len(report.get("details") or []) == 1 and detail.get("title"):
        return f"{report['candidate_name']}+{detail['title']}匹配报告"
    return f"候选人「{report['candidate_name']}」匹配报告"


def _detail_title(report: dict, detail: dict) -> str:
    candidate_name = detail.get("candidate_name") or report.get("candidate_name") or ""
    position_prefix = f"岗位{detail.get('seq_no')}：" if detail.get("seq_no") else ""
    position_title = f"{position_prefix}{detail.get('title', '')}"
    company = f"（{detail.get('company')}）" if detail.get("company") else ""
    prefix = f"候选人：{candidate_name}｜" if candidate_name else ""
    return f"{prefix}{position_title}{company}"


def _tmp_path(suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = Path(handle.name)
    handle.close()
    return path


def build_docx(report: dict) -> Path:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = "Microsoft YaHei"
    styles["Normal"].font.size = Pt(10.5)

    doc.add_heading(_title(report), level=1)
    doc.add_paragraph(
        f"模型 {report.get('model', '')} · {report.get('total_tokens', 0)} tokens · {report.get('finished_at') or ''}"
    )

    if report.get("details"):
        doc.add_heading("一、岗位逐项匹配明细", level=2)
        for detail in report["details"]:
            doc.add_heading(_detail_title(report, detail), level=3)
            summary = detail.get("score_summary") or {}
            if summary:
                if summary.get("dimensions"):
                    blocker = summary.get("blocker") or {}
                    if blocker.get("total", 0):
                        doc.add_paragraph(
                            f"关键门槛：{blocker.get('score', 0)}分（满足 {blocker.get('satisfied', 0)}/{blocker.get('total', 0)} 条，"
                            f"不满足 {blocker.get('failed', 0)} 条，待确认 {blocker.get('pending', 0)} 条）"
                        )
                    for dim in summary.get("dimensions") or []:
                        doc.add_paragraph(
                            f"{dim.get('label') or '评分维度'}：{dim.get('score', 0)}分（满足 {dim.get('satisfied', 0)}/{dim.get('total', 0)} 条，"
                            f"不满足 {dim.get('failed', 0)} 条，待确认 {dim.get('pending', 0)} 条）"
                        )
                    doc.add_paragraph(f"置信度：{summary.get('confidence', 0)}分")
                else:
                    hard = summary.get("hard") or {}
                    priority = summary.get("priority") or summary.get("soft") or {}
                    doc.add_paragraph(
                        f"硬性条件匹配得分：{hard.get('score', 0)}分（满足 {hard.get('satisfied', 0)}/{hard.get('total', 0)} 条，"
                        f"不满足 {hard.get('failed', 0)} 条，待确认 {hard.get('pending', 0)} 条）"
                    )
                    doc.add_paragraph(
                        f"优先条件匹配得分：{priority.get('score', 0)}分（满足 {priority.get('satisfied', 0)}/{priority.get('total', 0)} 条，"
                        f"不满足 {priority.get('failed', 0)} 条，待确认 {priority.get('pending', 0)} 条）"
                    )
                doc.add_paragraph(f"结论：{summary.get('judgement') or '需结合客户接受度判断'}")
            doc.add_paragraph(f"综合参考得分：约{detail.get('final_score')}分")

            def add_rows(title, rows, tail_header):
                doc.add_paragraph(title)
                table = doc.add_table(rows=1, cols=5)
                table.style = "Table Grid"
                for i, h in enumerate(("序号", "条件", "判断", "依据", tail_header)):
                    table.rows[0].cells[i].text = h
                if rows:
                    for idx, item in enumerate(rows, 1):
                        row = table.add_row().cells
                        note = item.get("note", "")
                        row[0].text = str(idx)
                        row[1].text = item.get("requirement", "")
                        row[2].text = item.get("label", "")
                        row[3].text = note
                        row[4].text = "—" if item.get("label") == "满足" else note
                else:
                    row = table.add_row().cells
                    row[0].text = "—"
                    row[1].text = "未拆出条件"
                    row[2].text = "待确认"
                    row[3].text = "输入信息不足"
                    row[4].text = "需人工确认"

            groups = CONDITION_GROUPS if summary.get("dimensions") else [
                ("core", "硬性条件", "风险/待确认点"),
                ("preference", "优先条件", "亮点/缺口"),
            ]
            for idx, (key, label, tail_header) in enumerate(groups, 1):
                rows = [
                    row for row in detail.get("rows", [])
                    if CONDITION_ALIASES.get(row.get("condition_type"), row.get("condition_type")) == key
                ]
                if not rows and len(groups) > 2:
                    continue
                add_rows(f"{idx}、{label}逐项匹配", rows, tail_header)
            suggestions = detail.get("suggestions") or []
            if suggestions:
                doc.add_paragraph("专业简历优化沟通建议：")
                for suggestion in suggestions:
                    doc.add_paragraph(suggestion.get("text", ""), style="List Bullet")

    doc.add_heading("二、人工审核队列", level=2)
    review_items = [item for item in report.get("details", []) if item.get("final_score", 0) >= settings.review_threshold]
    if review_items:
        table = doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        for i, h in enumerate(("岗位", "公司", "得分", "建议动作")):
            table.rows[0].cells[i].text = h
        for item in review_items:
            row = table.add_row().cells
            row[0].text = f"{'岗位' + str(item.get('seq_no')) + '：' if item.get('seq_no') else ''}{item.get('title', '')}"
            row[1].text = item.get("company") or "—"
            row[2].text = f"{item.get('final_score')}分"
            row[3].text = "建议推荐客户" if item.get("final_score", 0) >= settings.top_threshold else "进入人工审核"
    else:
        doc.add_paragraph(f"本次没有达到 {settings.review_threshold} 分的岗位，暂不进入人工审核。")

    path = _tmp_path(".docx")
    doc.save(path)
    return path


def _escape(value: object) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _score_sentence(label: str, data: dict) -> str:
    return (
        f"{label}：{data.get('score', 0)}分（满足 {data.get('satisfied', 0)}/{data.get('total', 0)} 条，"
        f"不满足 {data.get('failed', 0)} 条，待确认 {data.get('pending', 0)} 条）"
    )


def build_pdf(report: dict) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib import colors

    font_name = "Helvetica"
    for font_path in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ):
        if Path(font_path).exists():
            try:
                pdfmetrics.registerFont(TTFont("CJK", font_path))
                font_name = "CJK"
                break
            except Exception:
                pass

    path = _tmp_path(".pdf")
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    styles = getSampleStyleSheet()
    styles["Title"].fontName = font_name
    styles["Heading2"].fontName = font_name
    styles["BodyText"].fontName = font_name
    styles["BodyText"].fontSize = 10
    styles["BodyText"].leading = 15

    story = [Paragraph(_title(report), styles["Title"]), Spacer(1, 8)]
    story.append(Paragraph(_escape(f"模型 {report.get('model', '')} · {report.get('total_tokens', 0)} tokens · {report.get('finished_at') or ''}"), styles["BodyText"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("一、岗位逐项匹配明细", styles["Heading2"]))
    if not report.get("details"):
        story.append(Paragraph("本次匹配未生成可用明细，可能是模型调用失败或任务尚未完成。", styles["BodyText"]))
    for detail in report.get("details", []):
        story.append(Spacer(1, 8))
        story.append(Paragraph(_escape(_detail_title(report, detail)), styles["Heading2"]))
        summary = detail.get("score_summary") or {}
        if summary:
            if summary.get("dimensions"):
                blocker = summary.get("blocker") or {}
                if blocker.get("total", 0):
                    story.append(Paragraph(_escape(_score_sentence("关键门槛", blocker)), styles["BodyText"]))
                for dim in summary.get("dimensions") or []:
                    story.append(Paragraph(_escape(_score_sentence(dim.get("label") or "评分维度", dim)), styles["BodyText"]))
                if summary.get("confidence") is not None:
                    story.append(Paragraph(_escape(f"置信度：{summary.get('confidence')}分"), styles["BodyText"]))
            else:
                story.append(Paragraph(_escape(_score_sentence("硬性条件匹配得分", summary.get("hard") or {})), styles["BodyText"]))
                story.append(Paragraph(_escape(_score_sentence("优先条件匹配得分", summary.get("priority") or summary.get("soft") or {})), styles["BodyText"]))
            story.append(Paragraph(_escape(f"结论：{summary.get('judgement') or '需结合客户接受度判断'}"), styles["BodyText"]))
        story.append(Paragraph(_escape(f"综合参考得分：约{detail.get('final_score')}分"), styles["BodyText"]))

        groups = CONDITION_GROUPS if summary.get("dimensions") else [
            ("core", "硬性条件", "风险/待确认点"),
            ("preference", "优先条件", "亮点/缺口"),
        ]
        for condition_key, label, tail_header in groups:
            rows = [
                row for row in detail.get("rows", [])
                if CONDITION_ALIASES.get(row.get("condition_type"), row.get("condition_type")) == condition_key
            ]
            if not rows and len(groups) > 2:
                continue
            table_rows = [["序号", "条件", "判断", "依据", tail_header]]
            if rows:
                for idx, item in enumerate(rows, 1):
                    note = item.get("note", "")
                    table_rows.append([
                        str(idx),
                        item.get("requirement", ""),
                        item.get("label", ""),
                        note,
                        "—" if item.get("label") == "满足" else note,
                    ])
            else:
                table_rows.append(["—", "未拆出条件", "待确认", "输入信息不足", "需人工确认"])
            story.append(Paragraph(_escape(label), styles["BodyText"]))
            table = Table(table_rows, repeatRows=1, colWidths=[12 * mm, 42 * mm, 20 * mm, 54 * mm, 35 * mm])
            table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9DDE3")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F6F8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(table)
            story.append(Spacer(1, 6))
        suggestions = detail.get("suggestions") or []
        if suggestions:
            story.append(Paragraph("专业简历优化沟通建议：", styles["BodyText"]))
            for idx, suggestion in enumerate(suggestions, 1):
                story.append(Paragraph(_escape(f"{idx}. {suggestion.get('text', '')}"), styles["BodyText"]))
    doc.build(story)
    return path
