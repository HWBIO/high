import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..db import company_display_expr, get_conn, loads_or
from ..services import document_importer, feishu_store, llm, match_result_cleanup, operation_log
from ..services.positions_helpers import ensure_company
from .auth import _is_protected_super_admin, require_roles, require_user

router = APIRouter(prefix="/api/companies", tags=["companies"])

INDUSTRIES = (
    "AI", "互联网/IT", "电子/通信/半导体", "金融", "汽车", "能源/化工/环保",
    "医疗/生物医药", "机械/装备制造", "消费品制造", "房地产/建筑", "广告/传媒/文化",
    "专业服务", "教育/培训/科研", "贸易/零售", "交通/物流/仓储", "生活服务",
    "政府/公共事业", "农林牧渔", "其他行业",
)
STATUSES = ("开发中", "已签约", "历史客户")
require_company_operator = require_roles("business_admin", "consultant")

COMPANY_IMPORT_SYSTEM = """你是猎头客户文件解析助手。请从用户上传的客户/公司文档中提取客户信息。

只输出合法 JSON 对象，不要任何其他文字：

{
  "companies": [
    {
      "legal_name": "营业执照上的公司全称",
      "short_name": "公司简称/品牌简称，没有则空字符串",
      "city": "城市，没有则空字符串",
      "industry": "行业大类，没有则空字符串",
      "contact_name": "客户侧联系人，没有则空字符串",
      "status": "开发中",
      "notes": "客户简介、用人偏好、合作信息、风险点等原文摘要，没有则空字符串",
      "extra": {
        "website": "官网/网址，没有则空字符串",
        "address": "地址，没有则空字符串",
        "products": "主营业务/产品线，没有则空字符串",
        "scale": "人员规模，没有则空字符串",
        "strengths": "公司优势，没有则空字符串",
        "challenges": "公司劣势/挑战，没有则空字符串",
        "financing": "融资/上市信息，没有则空字符串"
      }
    }
  ]
}

规则：
- 文档中有多个客户/公司时必须全部提取。
- legal_name 是最低必需字段，必须使用营业执照名称；缺失时不要编造。
- short_name 只填写原文明确提供的简称或品牌简称，不要用不完整的公司名猜测。
- industry 尽量映射为系统行业大类之一；无法判断则空字符串。
- status 只能是 开发中/已签约/历史客户，无法判断填 开发中。
- 不要编造文档中不存在的联系人、城市、行业、融资等信息。"""


class CompanyIn(BaseModel):
    legal_name: str = ""
    name: str = ""  # 兼容旧客户端；新客户端使用 legal_name
    short_name: str = ""
    city: str = ""
    industry: str = ""
    contact_user_id: Optional[int] = None
    contact_name: str = ""
    status: str = "开发中"
    notes: str = ""
    extra: Optional[dict] = None


class CompanyUpdate(BaseModel):
    legal_name: Optional[str] = None
    name: Optional[str] = None
    short_name: Optional[str] = None
    city: Optional[str] = None
    industry: Optional[str] = None
    contact_user_id: Optional[int] = None
    contact_name: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    extra: Optional[dict] = None


def _page(limit: int = 100, offset: int = 0) -> tuple[int, int]:
    return max(1, min(int(limit or 100), 1000)), max(0, int(offset or 0))


def _company_cols(conn) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(companies)").fetchall()}


def _select_expr(cols: set[str], col: str, default: str = "''") -> str:
    return f"c.{col}" if col in cols else f"{default} AS {col}"


def _company_dict(row) -> dict:
    item = dict(row)
    item["extra"] = loads_or(item.pop("extra_json", ""), {})
    item["project_count"] = item.get("project_count") or 0
    item["position_count"] = item.get("position_count") or 0
    return item


@router.get("/meta")
def companies_meta(user=Depends(require_user)):
    conn = get_conn()
    cols = _company_cols(conn)
    users = conn.execute(
        "SELECT id, username, display_name, role, status FROM users ORDER BY id"
    ).fetchall()
    if not _is_protected_super_admin(user):
        users = [row for row in users if not _is_protected_super_admin(row)]
    cities = []
    if "city" in cols:
        cities = conn.execute(
            "SELECT DISTINCT city FROM companies WHERE city != '' ORDER BY city"
        ).fetchall()
    return {
        "industries": list(INDUSTRIES),
        "statuses": list(STATUSES),
        "cities": [row["city"] for row in cities],
        "users": [dict(row) for row in users],
    }


@router.get("")
def list_companies(
    keyword: str = "",
    city: str = "",
    industry: str = "",
    contact_user_id: Optional[int] = None,
    status: str = "",
    limit: int = 100,
    offset: int = 0,
):
    limit, offset = _page(limit, offset)
    conn = get_conn()
    cols = _company_cols(conn)
    where, params = [], []
    if keyword.strip():
        if "legal_name" in cols and "short_name" in cols:
            where.append("(c.legal_name LIKE ? OR c.short_name LIKE ? OR c.name LIKE ?)")
            params.extend([f"%{keyword.strip()}%"] * 3)
        else:
            where.append("c.name LIKE ?")
            params.append(f"%{keyword.strip()}%")
    if city.strip() and "city" in cols:
        where.append("c.city=?")
        params.append(city.strip())
    if industry.strip() and "industry" in cols:
        where.append("c.industry=?")
        params.append(industry.strip())
    if contact_user_id and "contact_user_id" in cols:
        where.append("c.contact_user_id=?")
        params.append(contact_user_id)
    if status.strip() and "status" in cols:
        where.append("c.status=?")
        params.append(status.strip())
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM companies c" + where_sql,
        params,
    ).fetchone()["c"]
    contact_join = " LEFT JOIN users u ON u.id=c.contact_user_id" if "contact_user_id" in cols else ""
    status_expr = _select_expr(cols, "status", "'开发中'")
    rows = conn.execute(
        "SELECT c.id, c.name,"
        f" {_select_expr(cols, 'legal_name')},"
        f" {_select_expr(cols, 'short_name')},"
        f" {_select_expr(cols, 'city')},"
        f" {_select_expr(cols, 'industry')},"
        f" {_select_expr(cols, 'contact_user_id', 'NULL')},"
        f" {_select_expr(cols, 'contact_name')},"
        f" {status_expr},"
        f" {_select_expr(cols, 'notes')},"
        f" {_select_expr(cols, 'extra_json')},"
        " c.created_at, c.updated_at,"
        f" {'u.display_name' if 'contact_user_id' in cols else 'NULL'} AS contact_display_name,"
        " COUNT(p.id) AS position_count,"
        " SUM(CASE WHEN p.is_project=1 AND p.status='active' THEN 1 ELSE 0 END) AS project_count"
        " FROM companies c"
        f"{contact_join}"
        " LEFT JOIN positions p ON p.company_id=c.id AND p.status='active'"
        f"{where_sql}"
        " GROUP BY c.id"
        " ORDER BY c.updated_at DESC, c.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {"items": [_company_dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/{company_id}")
def get_company(company_id: int):
    conn = get_conn()
    cols = _company_cols(conn)
    contact_join = " LEFT JOIN users u ON u.id=c.contact_user_id" if "contact_user_id" in cols else ""
    status_expr = _select_expr(cols, "status", "'开发中'")
    row = conn.execute(
        "SELECT c.id, c.name,"
        f" {_select_expr(cols, 'legal_name')},"
        f" {_select_expr(cols, 'short_name')},"
        f" {_select_expr(cols, 'city')},"
        f" {_select_expr(cols, 'industry')},"
        f" {_select_expr(cols, 'contact_user_id', 'NULL')},"
        f" {_select_expr(cols, 'contact_name')},"
        f" {status_expr},"
        f" {_select_expr(cols, 'notes')},"
        f" {_select_expr(cols, 'extra_json')},"
        " c.created_at, c.updated_at,"
        f" {'u.display_name' if 'contact_user_id' in cols else 'NULL'} AS contact_display_name,"
        " COUNT(p.id) AS position_count,"
        " SUM(CASE WHEN p.is_project=1 AND p.status='active' THEN 1 ELSE 0 END) AS project_count"
        " FROM companies c"
        f"{contact_join}"
        " LEFT JOIN positions p ON p.company_id=c.id AND p.status='active'"
        " WHERE c.id=? GROUP BY c.id",
        (company_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在")
    return _company_dict(row)


@router.post("")
def create_company(body: CompanyIn, _user=Depends(require_company_operator)):
    conn = get_conn()
    legal_name = (body.legal_name or body.name).strip()
    if not legal_name:
        raise HTTPException(400, "营业执照名称不能为空")
    if body.industry and body.industry not in INDUSTRIES:
        raise HTTPException(400, "行业取值非法")
    if body.status and body.status not in STATUSES:
        raise HTTPException(400, "状态取值非法")
    if conn.execute("SELECT id FROM companies WHERE legal_name=?", (legal_name,)).fetchone():
        raise HTTPException(409, f"营业执照名称「{legal_name}」已存在")
    company_id = conn.execute(
        "INSERT INTO companies (name, legal_name) VALUES (?,?)",
        (legal_name, legal_name),
    ).lastrowid
    cols = _company_cols(conn)
    fields, params = [], []
    for col, value in (
        ("short_name", body.short_name.strip()),
        ("city", body.city.strip()),
        ("industry", body.industry.strip()),
        ("contact_user_id", body.contact_user_id),
        ("contact_name", body.contact_name.strip()),
        ("status", body.status.strip() or "开发中"),
        ("notes", body.notes.strip()),
        ("extra_json", json.dumps(body.extra or {}, ensure_ascii=False)),
    ):
        if col in cols:
            fields.append(f"{col}=?")
            params.append(value)
    try:
        if fields:
            params.append(company_id)
            conn.execute(
                f"UPDATE companies SET {', '.join(fields)}, updated_at=datetime('now','localtime') WHERE id=?",
                params,
            )
        feishu_store.push_company(conn, company_id, commit_record_id=False)
        conn.commit()
        return {"id": company_id}
    except Exception:
        conn.rollback()
        raise


@router.post("/upload")
async def upload_companies(file: UploadFile = File(...), _user=Depends(require_company_operator)):
    """上传本地客户文件（PDF/DOCX/XLSX/TXT）→ LLM 解析 → 自动新增/更新客户。"""
    try:
        text, filename = await document_importer.extract_upload_text(file, min_chars=30)
    except document_importer.DocumentImportError as e:
        raise HTTPException(422, str(e)) from e
    try:
        parsed, _raw, _tokens, _latency = llm.chat_json(
            COMPANY_IMPORT_SYSTEM,
            f"可选行业大类：{', '.join(INDUSTRIES)}\n\n文件名：{filename}\n\n文件正文：\n{text[:50000]}",
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{filename}：DeepSeek 解析客户文件失败（{type(e).__name__}: {e}）") from e

    items = parsed.get("companies") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or not items:
        raise HTTPException(422, f"{filename}：未能解析出客户信息，请检查文件是否包含公司/客户资料")

    conn = get_conn()
    cols = _company_cols(conn)
    created, errors = [], []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {index} 条：解析结果不是对象，已跳过")
            continue
        legal_name = str(item.get("legal_name") or item.get("name") or "").strip()
        if not legal_name:
            errors.append(f"第 {index} 条：缺少营业执照名称，已跳过")
            continue
        industry = str(item.get("industry") or "").strip()
        if industry and industry not in INDUSTRIES:
            errors.append(f"客户「{legal_name}」：行业「{industry}」未在系统行业大类中，已置空")
            industry = ""
        status = str(item.get("status") or "开发中").strip()
        if status not in STATUSES:
            errors.append(f"客户「{legal_name}」：状态「{status}」非法，已按开发中导入")
            status = "开发中"
        try:
            conn.execute("SAVEPOINT company_import_item")
            company_id = ensure_company(conn, legal_name)
            fields, params = [], []
            for col, value in (
                ("legal_name", legal_name),
                ("short_name", str(item.get("short_name") or "").strip()),
                ("city", str(item.get("city") or "").strip()),
                ("industry", industry),
                ("contact_name", str(item.get("contact_name") or "").strip()),
                ("status", status),
                ("notes", str(item.get("notes") or "").strip()),
                ("extra_json", json.dumps(item.get("extra") or {}, ensure_ascii=False)),
            ):
                if col in cols:
                    fields.append(f"{col}=?")
                    params.append(value)
            if fields:
                params.append(company_id)
                conn.execute(
                    f"UPDATE companies SET {', '.join(fields)}, updated_at=datetime('now','localtime') WHERE id=?",
                    params,
                )
            feishu_store.push_company(conn, company_id, commit_record_id=False)
            created.append({"id": company_id, "name": legal_name})
            conn.execute("RELEASE SAVEPOINT company_import_item")
        except Exception as e:  # noqa: BLE001
            conn.execute("ROLLBACK TO SAVEPOINT company_import_item")
            conn.execute("RELEASE SAVEPOINT company_import_item")
            errors.append(f"客户「{legal_name}」入库失败（{type(e).__name__}: {e}）")
    if not created:
        conn.rollback()
        raise HTTPException(422, f"{filename}：没有客户成功导入；" + "；".join(errors))
    conn.commit()
    return {"count": len(created), "created": created, "errors": errors}


@router.put("/{company_id}")
def update_company(company_id: int, body: CompanyUpdate, _user=Depends(require_company_operator)):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name, legal_name, short_name FROM companies WHERE id=?",
        (company_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在")
    fields, params = [], []
    name_updated_by_legal_name = False
    requested_legal_name = body.legal_name if body.legal_name is not None else body.name
    if requested_legal_name is not None:
        legal_name = requested_legal_name.strip()
        duplicate = conn.execute(
            "SELECT id FROM companies WHERE legal_name=? AND id!=? AND TRIM(legal_name)!=''",
            (legal_name, company_id),
        ).fetchone()
        if duplicate:
            raise HTTPException(409, f"营业执照名称「{legal_name}」已存在")
        fields.append("legal_name=?")
        params.append(legal_name)
        if legal_name and (not row["legal_name"] or row["name"] == row["legal_name"]):
            fields.append("name=?")
            params.append(legal_name)
            name_updated_by_legal_name = True
    cols = _company_cols(conn)
    if body.short_name is not None and "short_name" in cols:
        fields.append("short_name=?")
        params.append(body.short_name.strip())
        if not name_updated_by_legal_name and row["name"] == (row["short_name"] or row["name"]):
            fields.append("name=?")
            params.append(body.short_name.strip() or row["name"])
    if body.city is not None and "city" in cols:
        fields.append("city=?")
        params.append(body.city.strip())
    if body.industry is not None and "industry" in cols:
        if body.industry and body.industry not in INDUSTRIES:
            raise HTTPException(400, "行业取值非法")
        fields.append("industry=?")
        params.append(body.industry.strip())
    if "contact_user_id" in body.model_fields_set and "contact_user_id" in cols:
        fields.append("contact_user_id=?")
        params.append(body.contact_user_id)
    if body.contact_name is not None and "contact_name" in cols:
        fields.append("contact_name=?")
        params.append(body.contact_name.strip())
    if body.status is not None and "status" in cols:
        if body.status and body.status not in STATUSES:
            raise HTTPException(400, "状态取值非法")
        fields.append("status=?")
        params.append(body.status.strip() or "开发中")
    if body.notes is not None and "notes" in cols:
        fields.append("notes=?")
        params.append(body.notes.strip())
    if body.extra is not None and "extra_json" in cols:
        old_extra = loads_or(
            conn.execute("SELECT extra_json FROM companies WHERE id=?", (company_id,)).fetchone()["extra_json"],
            {},
        )
        merged_extra = {**old_extra, **body.extra}
        fields.append("extra_json=?")
        params.append(json.dumps(merged_extra, ensure_ascii=False))
    if not fields:
        return {"updated": False}
    params.append(company_id)
    try:
        conn.execute(
            f"UPDATE companies SET {', '.join(fields)}, updated_at=datetime('now','localtime') WHERE id=?",
            params,
        )
        feishu_store.push_company(conn, company_id, commit_record_id=False)
        conn.commit()
        return {"updated": True}
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if "companies.legal_name" in str(exc) or "idx_companies_legal_name" in str(exc):
            raise HTTPException(409, "营业执照名称已存在") from exc
        raise
    except Exception:
        conn.rollback()
        raise


@router.delete("/{company_id}")
def delete_company(company_id: int, _user=Depends(require_company_operator)):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name, legal_name, short_name, feishu_record_id FROM companies WHERE id=?",
        (company_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在")
    positions = conn.execute(
        "SELECT id, feishu_record_id, feishu_project_record_id FROM positions WHERE company_id=?",
        (company_id,),
    ).fetchall()
    position_ids = [item["id"] for item in positions]
    result_ids = []
    try:
        if position_ids:
            placeholders = ",".join(["?"] * len(position_ids))
            result_ids = [
                item["id"] for item in conn.execute(
                    f"SELECT id FROM match_results WHERE position_id IN ({placeholders})",
                    position_ids,
                ).fetchall()
            ]
            for result_id in result_ids:
                match_result_cleanup.delete_match_result(conn, result_id)
            tracking_ids = [
                item["id"] for item in conn.execute(
                    f"SELECT id FROM candidate_tracking WHERE position_id IN ({placeholders})",
                    position_ids,
                ).fetchall()
            ]
            if tracking_ids:
                tracking_placeholders = ",".join(["?"] * len(tracking_ids))
                conn.execute(
                    f"DELETE FROM tracking_logs WHERE tracking_id IN ({tracking_placeholders})",
                    tracking_ids,
                )
            conn.execute(f"DELETE FROM candidate_tracking WHERE position_id IN ({placeholders})", position_ids)
            conn.execute(f"DELETE FROM prompts WHERE position_id IN ({placeholders})", position_ids)
            conn.execute(f"DELETE FROM positions WHERE id IN ({placeholders})", position_ids)
        conn.execute("DELETE FROM prompts WHERE company_id=?", (company_id,))
        operation_log.record(
            conn,
            _user,
            "delete",
            "company",
            company_id,
            row["legal_name"] or row["short_name"] or row["name"] or "",
            {"positions_deleted": len(position_ids), "match_results_deleted": len(result_ids) if position_ids else 0},
        )
        cur = conn.execute("DELETE FROM companies WHERE id=?", (company_id,))
        for item in positions:
            feishu_store.delete_position(item["feishu_record_id"])
            feishu_store.delete_project(item["feishu_project_record_id"])
        feishu_store.delete_company(row["feishu_record_id"])
        conn.commit()
        return {"deleted": True, "positions_deleted": len(position_ids), "rows_deleted": cur.rowcount}
    except RuntimeError as exc:
        conn.rollback()
        raise HTTPException(502, str(exc)) from exc
    except Exception:
        conn.rollback()
        raise


@router.get("/{company_id}/positions")
def company_positions(company_id: int, status: str = "active", is_project: Optional[int] = None):
    conn = get_conn()
    company_expr = company_display_expr(conn, "c")
    where = ["p.company_id=?"]
    params: list[object] = [company_id]
    if status in ("active", "archived"):
        where.append("p.status=?")
        params.append(status)
    if is_project is not None:
        where.append("p.is_project=?")
        params.append(1 if is_project else 0)
    rows = conn.execute(
        "SELECT p.id, p.company_id, p.seq_no, p.title, p.source, p.status, p.consultant_notes,"
        " p.headcount, p.salary_range, p.hard_requirements, p.nice_to_have,"
        " p.project_status, p.n_talents, p.n_recommend, p.n_interview, p.n_offer,"
        " p.consultant, p.is_project, p.hard_struct_json, p.project_json,"
        f" p.updated_at, {company_expr} AS company,"
        " (p.jd_struct_json != '') AS has_struct,"
        " EXISTS(SELECT 1 FROM prompts pr WHERE pr.layer='position' AND pr.position_id=p.id AND pr.is_active=1) AS has_position_prompt"
        " FROM positions p LEFT JOIN companies c ON c.id=p.company_id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY CAST(p.seq_no AS INTEGER), p.id",
        params,
    ).fetchall()
    return [dict(row) for row in rows]
