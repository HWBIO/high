import json

from fastapi import APIRouter

from ..db import company_display_expr, get_conn
router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects(status: str = "active"):
    """发起匹配页项目卡片的轻量数据源，只返回重点项目必要字段。"""
    conn = get_conn()
    company_expr = company_display_expr(conn, "c")
    where = ["p.is_project=1"]
    params = []
    if status in ("active", "archived"):
        where.append("p.status=?")
        params.append(status)
    rows = conn.execute(
        "SELECT p.id, p.seq_no, p.title, p.salary_range, p.project_json,"
        f" p.project_status, c.id AS company_id, {company_expr} AS company,"
        " EXISTS(SELECT 1 FROM prompts pr WHERE pr.layer='position' AND pr.position_id=p.id AND pr.is_active=1) AS has_position_prompt"
        " FROM positions p LEFT JOIN companies c ON c.id=p.company_id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY company, CAST(p.seq_no AS INTEGER), p.id",
        params,
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            project = json.loads(item.pop("project_json") or "{}")
        except json.JSONDecodeError:
            project = {}
        item["city"] = project.get("city") or ""
        item["industry"] = project.get("industry") or ""
        result.append(item)
    return result
