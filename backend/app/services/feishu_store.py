import json
import time
from typing import Any
from urllib.parse import urlparse

from ..config import settings
from ..db import company_display_expr, loads_or
from . import feishu
from .positions_helpers import ensure_company

_pause_until = 0.0
def enabled() -> bool:
    if _pause_until and _pause_until > time.time():
        return False
    status = feishu.config_status()
    ready = bool(settings.feishu_sync_enabled and status["ready"])
    if settings.feishu_sync_enabled and settings.feishu_sync_strict and not status["ready"]:
        missing = [
            name for name, item in status.get("tables", {}).items()
            if not item.get("configured")
        ]
        missing_text = f"；缺少表配置：{', '.join(missing)}" if missing else ""
        raise RuntimeError(f"飞书同步未就绪{missing_text}")
    return ready


def _warn_or_raise(message: str):
    global _pause_until
    if settings.feishu_sync_strict:
        raise RuntimeError(message)
    _pause_until = time.time() + 60


def is_record_not_found_error(exc: Exception) -> bool:
    text = str(exc)
    return "1254043" in text or "RecordIdNotFound" in text


def _commit_if_needed(conn, should_commit: bool) -> None:
    if should_commit:
        conn.commit()


def _public_url(path: str) -> str:
    base_url = settings.public_base_url.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{base_url}{normalized_path}"


def _mark_sync_job(conn, entity_type: str, entity_id: int, status: str, error: str = "") -> None:
    conn.execute(
        "INSERT INTO feishu_sync_jobs (entity_type, entity_id, action, status, attempts, last_error, updated_at)"
        " VALUES (?,?,'push',?,?,?,datetime('now','localtime'))"
        " ON CONFLICT(entity_type, entity_id, action) DO UPDATE SET"
        " status=excluded.status,"
        " attempts=CASE WHEN excluded.status='failed' THEN attempts+1 ELSE attempts END,"
        " last_error=excluded.last_error,"
        " updated_at=datetime('now','localtime')",
        (entity_type, entity_id, status, 1 if status == "failed" else 0, error[:1000]),
    )


def _mark_synced(conn, entity_type: str, entity_id: int) -> None:
    _mark_sync_job(conn, entity_type, entity_id, "synced")


def _handle_push_error(conn, entity_type: str, entity_id: int, message: str) -> None:
    _mark_sync_job(conn, entity_type, entity_id, "failed", message)
    _warn_or_raise(message)


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text_arr = item.get("text_arr")
                if isinstance(text_arr, list):
                    parts.append("".join(str(part.get("text") if isinstance(part, dict) else part) for part in text_arr))
                else:
                    parts.append(str(item.get("text") or item.get("name") or item.get("email") or item.get("id") or ""))
            else:
                parts.append(str(item))
        return "、".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("email") or value.get("id") or json.dumps(value, ensure_ascii=False))
    return str(value).strip()


def _pick(fields: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in fields:
            return _value(fields.get(name))
    return ""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(_value(value) or default))
    except ValueError:
        return default


def _bool_text(value: Any) -> int:
    text = _value(value)
    return 1 if text in ("1", "true", "True", "是", "重点", "重点项目", "进行中") else 0


def _app_company_status(text: str) -> str:
    return {
        "意向中": "开发中",
        "合作中": "已签约",
        "已暂停": "历史客户",
        "已终止": "历史客户",
    }.get(text, text or "开发中")


def _feishu_company_status(text: str) -> str:
    return {
        "开发中": "意向中",
        "已签约": "合作中",
        "历史客户": "已终止",
    }.get(text, text or "意向中")


def _app_position_status(text: str) -> str:
    return "archived" if text in ("已暂停", "已关闭", "已完成") else "active"


def _feishu_position_status(text: str) -> str:
    return "已关闭" if text == "archived" else "招聘中"


def _app_project_status(text: str) -> str:
    return {
        "待开始": "未开始",
        "待启动": "未开始",
        "进展中": "进行中",
        "Live": "进行中",
        "live": "进行中",
        "已暂停": "已结束",
        "已完成": "已结束",
        "已取消": "已结束",
    }.get(text, text or "未开始")


def _feishu_project_status(text: str) -> str:
    return {
        "未开始": "待启动",
        "进行中": "进行中",
        "已结束": "已完成",
    }.get(text, text or "待启动")


def _rows(table_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = ""
    while True:
        data = feishu.client.list_records(table_id, page_size=200, page_token=page_token)
        items.extend(data.get("items") or [])
        if not data.get("has_more"):
            return items
        page_token = data.get("page_token") or ""
        if not page_token:
            return items


def try_pull_companies(conn) -> None:
    if not enabled():
        return
    try:
        for record in _rows(settings.feishu_table_companies):
            fields = record.get("fields") or {}
            legal_name = _pick(fields, "营业执照名称", "工商注册名称")
            short_name = _pick(
                fields,
                "公司简称", "简称", "品牌简称", "公司名", "客户名称", "公司名称", "客户名", "客户",
            )
            identity_name = legal_name or short_name
            if not identity_name:
                continue
            local_id = _int(_pick(fields, "本地ID", "local_id"), 0)
            extra = {
                "website": _pick(fields, "网址", "官网"),
                "address": _pick(fields, "地址"),
                "products": _pick(fields, "主营业务", "产品线", "公司产品"),
                "scale": _pick(fields, "人员规模"),
                "strengths": _pick(fields, "公司优势", "吸引人才亮点"),
                "challenges": _pick(fields, "公司劣势", "风险点", "挑战"),
                "financing": _pick(fields, "融资情况", "上市市值"),
            }
            payload = (
                legal_name,
                short_name,
                _pick(fields, "城市", "地区", "客户地址"),
                _pick(fields, "行业", "行业大类", "客户行业"),
                _pick(fields, "外部联系人", "客户侧联系人", "联系人"),
                _app_company_status(_pick(fields, "状态", "合作状态")),
                _pick(fields, "客户备注", "备注", "客户简介"),
                json.dumps({k: v for k, v in extra.items() if v}, ensure_ascii=False),
                record.get("record_id") or "",
            )
            if local_id:
                local = conn.execute(
                    "SELECT id, name, legal_name, short_name FROM companies WHERE id=?",
                    (local_id,),
                ).fetchone()
                if local:
                    merged_payload = (
                        legal_name or local["legal_name"] or "",
                        short_name or local["short_name"] or local["name"] or "",
                        *payload[2:],
                    )
                    conn.execute(
                        "UPDATE companies SET legal_name=?, short_name=?, city=?, industry=?, contact_name=?, status=?, notes=?,"
                        " extra_json=?, feishu_record_id=?, updated_at=datetime('now','localtime') WHERE id=?",
                        (*merged_payload, local_id),
                    )
                    continue
            row = conn.execute(
                "SELECT id, name, legal_name, short_name FROM companies"
                " WHERE (? != '' AND legal_name=?) OR short_name=? OR name=? ORDER BY id LIMIT 1",
                (legal_name, legal_name, short_name, identity_name),
            ).fetchone()
            if row:
                merged_payload = (
                    legal_name or row["legal_name"] or "",
                    short_name or row["short_name"] or row["name"] or "",
                    *payload[2:],
                )
                conn.execute(
                    "UPDATE companies SET legal_name=?, short_name=?, city=?, industry=?, contact_name=?, status=?, notes=?,"
                    " extra_json=?, feishu_record_id=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (*merged_payload, row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO companies (name, legal_name, short_name, city, industry, contact_name, status, notes, extra_json, feishu_record_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (identity_name, *payload),
                )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _warn_or_raise(f"飞书客户表同步失败：{exc}")


def _company_fields(row: dict[str, Any]) -> dict[str, Any]:
    extra = loads_or(row.get("extra_json") or "", {})
    return {
        "本地ID": row.get("id"),
        "营业执照名称": row.get("legal_name") or "",
        "公司名": row.get("short_name") or row.get("legal_name") or row.get("name") or "",
        "公司简称": row.get("short_name") or "",
        "行业": row.get("industry") or "",
        "城市": row.get("city") or "",
        "联系人": row.get("contact_display_name") or row.get("contact_name") or "",
        "合作状态": _feishu_company_status(row.get("status") or ""),
        "客户备注": row.get("notes") or "",
        "客户状态": extra.get("companyStatus") or "",
        "联系人职位": extra.get("contactTitle") or "",
        "手机": extra.get("mobile") or "",
        "邮箱": extra.get("email") or "",
        "微信": extra.get("wechat") or "",
        "电话": extra.get("phone") or "",
        "网址": extra.get("website") or "",
        "地址": extra.get("address") or "",
        "主营业务": extra.get("products") or "",
        "产品线": extra.get("products") or "",
        "福利": extra.get("benefits") or "",
        "人员规模": extra.get("scale") or "",
        "主要竞争对手": extra.get("competitors") or "",
        "公司优势": extra.get("strengths") or "",
        "公司劣势": extra.get("challenges") or "",
        "融资情况": extra.get("financing") or "",
        "成立时间": extra.get("foundedAt") or "",
        "开票抬头": extra.get("invoiceTitle") or "",
    }


def push_company(conn, company_id: int, commit_record_id: bool = True) -> None:
    if not enabled():
        return
    try:
        row = conn.execute(
            "SELECT c.*, u.display_name AS contact_display_name,"
            " COUNT(p.id) AS position_count,"
            " SUM(CASE WHEN p.is_project=1 AND p.status='active' THEN 1 ELSE 0 END) AS project_count"
            " FROM companies c LEFT JOIN users u ON u.id=c.contact_user_id"
            " LEFT JOIN positions p ON p.company_id=c.id AND p.status='active'"
            " WHERE c.id=? GROUP BY c.id",
            (company_id,),
        ).fetchone()
        if not row:
            return
        data = dict(row)
        fields = _company_fields(data)
        record_id = data.get("feishu_record_id") or ""
        if record_id:
            feishu.client.update_record(settings.feishu_table_companies, record_id, fields)
        else:
            created = feishu.client.create_records(settings.feishu_table_companies, [fields])
            items = created.get("records") or []
            new_id = (items[0] or {}).get("record_id") if items else ""
            if new_id:
                conn.execute("UPDATE companies SET feishu_record_id=? WHERE id=?", (new_id, company_id))
        _mark_synced(conn, "company", company_id)
        _commit_if_needed(conn, commit_record_id)
    except Exception as exc:  # noqa: BLE001
        _handle_push_error(conn, "company", company_id, f"飞书客户写入失败：{exc}")


def delete_company(company_record_id: str) -> None:
    if not enabled() or not company_record_id:
        return
    try:
        feishu.client.delete_record(settings.feishu_table_companies, company_record_id)
    except Exception as exc:  # noqa: BLE001
        if is_record_not_found_error(exc):
            return
        _warn_or_raise(f"飞书客户删除失败：{exc}")


def _feishu_consultant_role(role: str) -> str:
    return {
        "super_admin": "管理员",
        "business_admin": "管理员",
        "consultant": "猎头顾问",
    }.get(role, "猎头顾问")


def _feishu_employment_status(status: str) -> str:
    return "离职" if status == "disabled" else "在职"


def push_user(conn, user_id: int) -> None:
    if not enabled():
        return
    try:
        row = conn.execute(
            "SELECT id, username, display_name, role, status, feishu_record_id"
            " FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not row:
            return
        data = dict(row)
        fields = {
            "本地ID": data.get("id"),
            "用户名": data.get("username") or "",
            "显示名称": data.get("display_name") or data.get("username") or "",
            "顾问姓名": data.get("display_name") or data.get("username") or "",
            "角色": data.get("role") or "consultant",
            "顾问角色": _feishu_consultant_role(data.get("role") or ""),
            "状态": data.get("status") or "active",
            "在职状态": _feishu_employment_status(data.get("status") or ""),
        }
        record_id = data.get("feishu_record_id") or ""
        if record_id:
            feishu.client.update_record(settings.feishu_table_users, record_id, fields)
        else:
            created = feishu.client.create_records(settings.feishu_table_users, [fields])
            items = created.get("records") or []
            new_id = (items[0] or {}).get("record_id") if items else ""
            if new_id:
                conn.execute("UPDATE users SET feishu_record_id=? WHERE id=?", (new_id, user_id))
                conn.commit()
    except Exception as exc:  # noqa: BLE001
        _warn_or_raise(f"飞书用户顾问写入失败：{exc}")


def _app_user_role(text: str) -> str:
    return {
        "管理员": "business_admin",
        "猎头顾问": "consultant",
    }.get(text, text if text in ("super_admin", "business_admin", "consultant") else "consultant")


def _app_user_status(text: str) -> str:
    return {
        "离职": "disabled",
        "停用": "disabled",
        "休假": "active",
        "在职": "active",
    }.get(text, text if text in ("pending", "active", "disabled") else "active")


def try_pull_users(conn) -> None:
    if not enabled():
        return
    try:
        for record in _rows(settings.feishu_table_users):
            fields = record.get("fields") or {}
            username = _pick(fields, "用户名", "账号", "手机号", "邮箱")
            display_name = _pick(fields, "显示名称", "顾问姓名", "姓名", "用户名")
            if not username and display_name:
                username = display_name
            if not username:
                continue
            role = _app_user_role(_pick(fields, "角色", "顾问角色"))
            status = _app_user_status(_pick(fields, "状态", "在职状态"))
            local_id = _int(_pick(fields, "本地ID", "local_id"), 0)
            record_id = record.get("record_id") or ""
            payload = (username, display_name or username, role, status, record_id)
            if local_id:
                cur = conn.execute(
                    "UPDATE users SET username=?, display_name=?, role=?, status=?, feishu_record_id=? WHERE id=?",
                    (*payload, local_id),
                )
                if cur.rowcount:
                    continue
            existing = conn.execute(
                "SELECT id FROM users WHERE feishu_record_id=? OR username=? ORDER BY id LIMIT 1",
                (record_id, username),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE users SET username=?, display_name=?, role=?, status=?, feishu_record_id=? WHERE id=?",
                    (*payload, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO users (username, password_hash, display_name, role, status, feishu_record_id)"
                    " VALUES (?, '', ?, ?, ?, ?)",
                    payload,
                )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _warn_or_raise(f"飞书用户顾问表同步失败：{exc}")


def _position_city(project_json: str) -> str:
    return loads_or(project_json, {}).get("city") or ""


def try_pull_positions(conn) -> None:
    if not enabled():
        return
    try_pull_companies(conn)
    try:
        for record in _rows(settings.feishu_table_positions):
            fields = record.get("fields") or {}
            title = _pick(fields, "岗位名称", "职位名称", "项目名称")
            if not title:
                continue
            company = _pick(fields, "客户", "岗位公司", "公司", "公司名称", "客户公司", "所属客户")
            company_id = ensure_company(conn, company)
            project = {
                "city": _pick(fields, "城市", "工作城市", "工作地点"),
                "address": _pick(fields, "工作地址"),
                "industry": _pick(fields, "行业"),
            }
            local_id = _int(_pick(fields, "本地ID", "local_id"), 0)
            payload = (
                company_id,
                _pick(fields, "序号"),
                title,
                _pick(fields, "JD原文", "岗位JD", "岗位要求", "职责要求", "岗位职责", "任职要求"),
                _pick(fields, "顾问备注", "备注"),
                _pick(fields, "薪资", "薪资范围"),
                _pick(fields, "硬性要求", "任职要求"),
                _pick(fields, "优先条件", "加分项"),
                _int(_pick(fields, "招聘数量"), 1) or 1,
                _app_project_status(_pick(fields, "项目状态")),
                _int(_pick(fields, "人才数量"), 0),
                _int(_pick(fields, "推荐"), 0),
                _int(_pick(fields, "面试"), 0),
                _int(_pick(fields, "offer", "Offer"), 0),
                _pick(fields, "顾问", "负责顾问"),
                _bool_text(_pick(fields, "重点项目")),
                _app_position_status(_pick(fields, "状态", "岗位状态")),
                json.dumps(project, ensure_ascii=False),
                record.get("record_id") or "",
            )
            if local_id:
                cur = conn.execute(
                    "UPDATE positions SET company_id=?, seq_no=?, title=?, jd_raw=?, consultant_notes=?, salary_range=?,"
                    " hard_requirements=?, nice_to_have=?, headcount=?, project_status=?, n_talents=?, n_recommend=?,"
                    " n_interview=?, n_offer=?, consultant=?, is_project=?, status=?, project_json=?, feishu_record_id=?,"
                    " updated_at=datetime('now','localtime') WHERE id=?",
                    (*payload, local_id),
                )
                if cur.rowcount:
                    continue
            existing = conn.execute(
                "SELECT id FROM positions WHERE title=? AND company_id=? AND status='active' ORDER BY id LIMIT 1",
                (title, company_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE positions SET seq_no=?, jd_raw=?, consultant_notes=?, salary_range=?, hard_requirements=?,"
                    " nice_to_have=?, headcount=?, project_status=?, n_talents=?, n_recommend=?, n_interview=?,"
                    " n_offer=?, consultant=?, is_project=?, status=?, project_json=?, feishu_record_id=?,"
                    " updated_at=datetime('now','localtime') WHERE id=?",
                    (*payload[1:], existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO positions (company_id, source, seq_no, title, jd_raw, consultant_notes, salary_range,"
                    " hard_requirements, nice_to_have, headcount, project_status, n_talents, n_recommend, n_interview,"
                    " n_offer, consultant, is_project, status, project_json, feishu_record_id)"
                    " VALUES (?,'manual',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    payload,
                )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _warn_or_raise(f"飞书岗位表同步失败：{exc}")


def _position_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "本地ID": row.get("id"),
        "岗位名称": row.get("title") or "",
        "客户": row.get("company") or "",
        "薪资范围": row.get("salary_range") or "",
        "城市": _position_city(row.get("project_json") or ""),
        "招聘数量": row.get("headcount") or 1,
        "项目状态": row.get("project_status") or "未开始",
        "顾问": row.get("consultant") or "",
        "硬性要求": row.get("hard_requirements") or "",
        "优先条件": row.get("nice_to_have") or "",
        "JD原文": row.get("jd_raw") or "",
        "状态": _feishu_position_status(row.get("status") or ""),
        "重点项目": "是" if row.get("is_project") else "否",
    }


def push_position(conn, position_id: int, commit_record_id: bool = True) -> None:
    if not enabled():
        return
    try:
        company_expr = company_display_expr(conn, "c")
        row = conn.execute(
            f"SELECT p.*, {company_expr} AS company FROM positions p LEFT JOIN companies c ON c.id=p.company_id WHERE p.id=?",
            (position_id,),
        ).fetchone()
        if not row:
            return
        data = dict(row)
        fields = _position_fields(data)
        record_id = data.get("feishu_record_id") or ""
        if record_id:
            feishu.client.update_record(settings.feishu_table_positions, record_id, fields)
        else:
            created = feishu.client.create_records(settings.feishu_table_positions, [fields])
            items = created.get("records") or []
            new_id = (items[0] or {}).get("record_id") if items else ""
            if new_id:
                conn.execute("UPDATE positions SET feishu_record_id=? WHERE id=?", (new_id, position_id))
        if data.get("is_project") and settings.feishu_table_projects:
            _push_project(conn, data, commit_record_id=commit_record_id)
        _mark_synced(conn, "position", position_id)
        _commit_if_needed(conn, commit_record_id)
    except Exception as exc:  # noqa: BLE001
        _handle_push_error(conn, "position", position_id, f"飞书岗位写入失败：{exc}")


def _push_project(conn, position: dict[str, Any], commit_record_id: bool = True) -> None:
    fields = {
        "本地岗位ID": position.get("id"),
        "项目名称": position.get("title") or "",
        "客户": position.get("company") or "",
        "城市": _position_city(position.get("project_json") or ""),
        "薪资": position.get("salary_range") or "",
        "项目状态": _feishu_project_status(position.get("project_status") or ""),
        "顾问": position.get("consultant") or "",
        "项目描述": position.get("consultant_notes") or "",
        "招聘人数": position.get("headcount") or 1,
    }
    record_id = position.get("feishu_project_record_id") or ""
    if record_id:
        feishu.client.update_record(settings.feishu_table_projects, record_id, fields)
    else:
        created = feishu.client.create_records(settings.feishu_table_projects, [fields])
        items = created.get("records") or []
        new_id = (items[0] or {}).get("record_id") if items else ""
        if new_id:
            conn.execute("UPDATE positions SET feishu_project_record_id=? WHERE id=?", (new_id, position.get("id")))


def delete_position(position_record_id: str) -> None:
    if not enabled() or not position_record_id:
        return
    try:
        feishu.client.delete_record(settings.feishu_table_positions, position_record_id)
    except Exception as exc:  # noqa: BLE001
        if is_record_not_found_error(exc):
            return
        _warn_or_raise(f"飞书岗位删除失败：{exc}")


def delete_project(project_record_id: str) -> None:
    if not enabled() or not project_record_id:
        return
    try:
        feishu.client.delete_record(settings.feishu_table_projects, project_record_id)
    except Exception as exc:  # noqa: BLE001
        if is_record_not_found_error(exc):
            return
        _warn_or_raise(f"飞书项目删除失败：{exc}")


def try_pull_prompts(conn) -> None:
    if not enabled():
        return
    try:
        for record in _rows(settings.feishu_table_prompts):
            fields = record.get("fields") or {}
            layer = _pick(fields, "层级", "类型", "提示词类型")
            layer = {
                "通用提示词": "general",
                "公司提示词": "company",
                "岗位提示词": "position",
                "岗位匹配": "general",
            }.get(layer, layer)
            content = _pick(fields, "提示词内容", "内容", "prompt", "Prompt")
            if layer not in ("general", "company", "position") or not content:
                continue
            company_id = None
            position_id = None
            company = _pick(fields, "公司", "客户")
            if layer == "company" and company:
                company_id = ensure_company(conn, company)
            position_title = _pick(fields, "岗位", "项目", "岗位名称", "项目名称")
            if layer == "position" and position_title:
                row = conn.execute("SELECT id FROM positions WHERE title=? ORDER BY id LIMIT 1", (position_title,)).fetchone()
                position_id = row["id"] if row else None
            version = _int(_pick(fields, "版本", "version", "版本号"), 1) or 1
            is_active = 1 if (_pick(fields, "是否生效", "生效状态", "状态") or "启用") in ("是", "启用", "1", "true", "True") else 0
            local_id = _int(_pick(fields, "本地ID", "local_id"), 0)
            payload = (layer, company_id, position_id, content, is_active, version, _pick(fields, "更新人"), "company_default", record.get("record_id") or "")
            if local_id:
                cur = conn.execute(
                    "UPDATE prompts SET layer=?, company_id=?, position_id=?, content=?, is_active=?, version=?,"
                    " updated_by=?, scope=?, feishu_record_id=? WHERE id=? AND scope='company_default'",
                    (*payload, local_id),
                )
                if cur.rowcount:
                    continue
            existing = conn.execute(
                "SELECT id FROM prompts WHERE feishu_record_id=? OR "
                "(layer=? AND company_id IS ? AND position_id IS ? AND version=? AND scope='company_default')"
                " ORDER BY id LIMIT 1",
                (record.get("record_id") or "", layer, company_id, position_id, version),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE prompts SET layer=?, company_id=?, position_id=?, content=?, is_active=?, version=?,"
                    " updated_by=?, scope=?, feishu_record_id=? WHERE id=?",
                    (*payload, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO prompts (layer, company_id, position_id, content, is_active, version, updated_by, scope, feishu_record_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    payload,
                )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _warn_or_raise(f"飞书提示词表同步失败：{exc}")


def push_prompt(conn, prompt_id: int, commit_record_id: bool = True) -> None:
    if not enabled():
        return
    try:
        company_expr = company_display_expr(conn, "c")
        row = conn.execute(
            f"SELECT pr.*, {company_expr} AS company, p.title AS position_title FROM prompts pr"
            " LEFT JOIN companies c ON c.id=pr.company_id"
            " LEFT JOIN positions p ON p.id=pr.position_id WHERE pr.id=?",
            (prompt_id,),
        ).fetchone()
        if not row:
            return
        data = dict(row)
        if data.get("scope") == "personal":
            return
        fields = {
            "本地ID": data.get("id"),
            "层级": {"general": "通用提示词", "company": "公司提示词", "position": "岗位提示词"}.get(data.get("layer"), data.get("layer")),
            "提示词名称": f"v{data.get('version') or 1}",
            "提示词类型": "岗位匹配",
            "公司": data.get("company") or "",
            "岗位": data.get("position_title") or "",
            "提示词内容": data.get("content") or "",
            "版本": data.get("version") or 1,
            "版本号": f"v{data.get('version') or 1}",
            "是否生效": "是" if data.get("is_active") else "否",
            "状态": "启用" if data.get("is_active") else "停用",
            "更新人": data.get("updated_by") or "",
        }
        record_id = data.get("feishu_record_id") or ""
        if record_id:
            feishu.client.update_record(settings.feishu_table_prompts, record_id, fields)
        else:
            created = feishu.client.create_records(settings.feishu_table_prompts, [fields])
            items = created.get("records") or []
            new_id = (items[0] or {}).get("record_id") if items else ""
            if new_id:
                conn.execute("UPDATE prompts SET feishu_record_id=? WHERE id=?", (new_id, prompt_id))
        _mark_synced(conn, "prompt", prompt_id)
        _commit_if_needed(conn, commit_record_id)
    except Exception as exc:  # noqa: BLE001
        _handle_push_error(conn, "prompt", prompt_id, f"飞书提示词写入失败：{exc}")


def push_match_result(conn, result_id: int) -> None:
    if not enabled():
        return
    try:
        company_expr = company_display_expr(conn, "co")
        row = conn.execute(
            "SELECT mr.*, c.name AS candidate_name, c.phone AS candidate_phone, c.resume_file,"
            f" p.title AS position_title, {company_expr} AS company"
            " FROM match_results mr"
            " JOIN match_runs r ON r.id=mr.run_id"
            " JOIN candidates c ON c.id=r.candidate_id"
            " JOIN positions p ON p.id=mr.position_id"
            " LEFT JOIN companies co ON co.id=p.company_id"
            " WHERE mr.id=?",
            (result_id,),
        ).fetchone()
        if not row:
            return
        data = dict(row)
        fields = {
            "本地ID": data.get("id"),
            "候选人姓名": data.get("candidate_name") or "",
            "匹配得分": data.get("final_score") or 0,
            "匹配岗位": data.get("position_title") or "",
            "匹配说明": data.get("exclude_reason") or data.get("raw_llm_output") or "",
            "推荐状态": {
                "pending": "待推荐",
                "approved": "已推荐",
                "rejected": "已淘汰",
                "needs_info": "待推荐",
            }.get(data.get("review_status"), "待推荐"),
            "备注": f"{data.get('candidate_name') or ''} + {data.get('company') or ''} {data.get('position_title') or ''}".strip(),
        }
        if report_url := _public_url(f"/results/{data.get('id')}"):
            fields["匹配报告"] = {"text": "查看匹配报告", "link": report_url}
        record_id = data.get("feishu_record_id") or ""
        if record_id:
            feishu.client.update_record(settings.feishu_table_match_results, record_id, fields)
        else:
            created = feishu.client.create_records(settings.feishu_table_match_results, [fields])
            items = created.get("records") or []
            new_id = (items[0] or {}).get("record_id") if items else ""
            if new_id:
                conn.execute("UPDATE match_results SET feishu_record_id=? WHERE id=?", (new_id, result_id))
                conn.commit()
    except Exception as exc:  # noqa: BLE001
        _warn_or_raise(f"飞书匹配结果写入失败：{exc}")


def push_client_recommendation(conn, report_id: int) -> None:
    if not enabled():
        return
    try:
        company_expr = company_display_expr(conn, "co")
        row = conn.execute(
            f"SELECT cr.*, c.name AS candidate_name, p.title AS position_title, {company_expr} AS company"
            " FROM client_recommendation_reports cr"
            " JOIN match_results mr ON mr.id=cr.result_id"
            " JOIN match_runs r ON r.id=mr.run_id"
            " JOIN candidates c ON c.id=r.candidate_id"
            " JOIN positions p ON p.id=mr.position_id"
            " LEFT JOIN companies co ON co.id=p.company_id"
            " WHERE cr.id=?",
            (report_id,),
        ).fetchone()
        if not row:
            return
        data = dict(row)
        title = f"{data.get('candidate_name') or ''}+{data.get('position_title') or ''}客户推荐报告"
        fields = {
            "推荐报告名称": title.strip("+"),
            "本地ID": data.get("id"),
            "匹配结果ID": data.get("result_id"),
            "候选人姓名": data.get("candidate_name") or "",
            "推荐岗位": data.get("position_title") or "",
            "客户": data.get("company") or "",
            "客户推荐报告": data.get("markdown") or "",
            "模型": data.get("model") or "",
            "Tokens": data.get("tokens") or 0,
            "版本": data.get("version") or 1,
            "状态": data.get("status") or "草稿",
        }
        if report_url := _public_url(f"/client-reports/{data.get('result_id')}"):
            fields["报告链接"] = {"text": "查看推荐报告", "link": report_url}
        record_id = data.get("feishu_record_id") or ""
        if record_id:
            feishu.client.update_record(settings.feishu_table_client_recommendations, record_id, fields)
        else:
            created = feishu.client.create_records(settings.feishu_table_client_recommendations, [fields])
            items = created.get("records") or []
            new_id = (items[0] or {}).get("record_id") if items else ""
            if new_id:
                conn.execute(
                    "UPDATE client_recommendation_reports SET feishu_record_id=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (new_id, report_id),
                )
                conn.commit()
    except Exception as exc:  # noqa: BLE001
        _warn_or_raise(f"飞书客户推荐报告写入失败：{exc}")


def try_pull_match_results(conn) -> None:
    if not enabled():
        return
    try:
        for record in _rows(settings.feishu_table_match_results):
            fields = record.get("fields") or {}
            local_id = _int(_pick(fields, "本地ID", "local_id"), 0)
            record_id = record.get("record_id") or ""
            if not local_id and not record_id:
                continue
            score_text = _pick(fields, "匹配得分", "匹配度评分", "综合参考得分", "得分")
            score = _int(score_text, 0)
            review_status = {
                "待推荐": "pending",
                "已推荐": "approved",
                "已淘汰": "rejected",
                "待补充": "needs_info",
                "待补充信息": "needs_info",
            }.get(_pick(fields, "推荐状态", "审核状态"), "")
            note = _pick(fields, "匹配说明", "备注")
            update_fields = ["feishu_record_id=?"]
            params: list[Any] = [record_id]
            if score_text != "":
                update_fields.append("final_score=?")
                params.append(score)
            if review_status:
                update_fields.append("review_status=?")
                params.append(review_status)
            if note:
                update_fields.append("exclude_reason=?")
                params.append(note)
            if local_id:
                params.append(local_id)
                conn.execute(
                    f"UPDATE match_results SET {', '.join(update_fields)} WHERE id=?",
                    params,
                )
            else:
                params.append(record_id)
                conn.execute(
                    f"UPDATE match_results SET {', '.join(update_fields)} WHERE feishu_record_id=?",
                    params,
                )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _warn_or_raise(f"飞书匹配结果表同步失败：{exc}")


def try_pull_client_recommendations(conn) -> None:
    if not enabled():
        return
    try:
        for record in _rows(settings.feishu_table_client_recommendations):
            fields = record.get("fields") or {}
            local_id = _int(_pick(fields, "本地ID", "local_id"), 0)
            result_id = _int(_pick(fields, "匹配结果ID"), 0)
            if not local_id and not result_id:
                continue
            record_id = record.get("record_id") or ""
            markdown = _pick(fields, "客户推荐报告")
            status = _pick(fields, "状态") or "草稿"
            update_fields = ["feishu_record_id=?", "updated_at=datetime('now','localtime')"]
            params: list[Any] = [record_id]
            if markdown:
                update_fields.append("markdown=?")
                params.append(markdown)
            if status:
                update_fields.append("status=?")
                params.append(status)
            if local_id:
                params.append(local_id)
                conn.execute(
                    f"UPDATE client_recommendation_reports SET {', '.join(update_fields)} WHERE id=?",
                    params,
                )
            elif result_id:
                params.append(result_id)
                conn.execute(
                    f"UPDATE client_recommendation_reports SET {', '.join(update_fields)} WHERE result_id=?",
                    params,
                )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _warn_or_raise(f"飞书客户推荐报告表同步失败：{exc}")


def _chat_session_key(user_id: int, run_ids_json: str) -> str:
    return f"{user_id}:{run_ids_json or '[]'}"


def push_chat_message(conn, message_id: int) -> None:
    if not enabled():
        return
    try:
        row = conn.execute(
            "SELECT m.*, s.user_id, s.run_ids_json, s.title, u.display_name, u.username"
            " FROM chat_messages m"
            " JOIN chat_sessions s ON s.id=m.session_id"
            " LEFT JOIN users u ON u.id=s.user_id"
            " WHERE m.id=?",
            (message_id,),
        ).fetchone()
        if not row:
            return
        data = dict(row)
        content = data.get("content") or ""
        fields = {
            "消息标题": f"{data.get('title') or '匹配报告对话'}-{data.get('role') or ''}-{data.get('id')}",
            "本地消息ID": data.get("id"),
            "会话ID": data.get("session_id"),
            "会话Key": _chat_session_key(data.get("user_id") or 0, data.get("run_ids_json") or "[]"),
            "用户ID": data.get("user_id") or 0,
            "用户名称": data.get("display_name") or data.get("username") or "",
            "Run IDs": data.get("run_ids_json") or "[]",
            "角色": data.get("role") or "",
            "消息内容": content,
            "模型": data.get("model") or "",
            "Tokens": data.get("tokens") or 0,
        }
        record_id = data.get("feishu_record_id") or ""
        if record_id:
            feishu.client.update_record(settings.feishu_table_chat_messages, record_id, fields)
        else:
            created = feishu.client.create_records(settings.feishu_table_chat_messages, [fields])
            items = created.get("records") or []
            new_id = (items[0] or {}).get("record_id") if items else ""
            if new_id:
                conn.execute("UPDATE chat_messages SET feishu_record_id=? WHERE id=?", (new_id, message_id))
                conn.commit()
    except Exception as exc:  # noqa: BLE001
        _warn_or_raise(f"飞书 AI 对话记录写入失败：{exc}")


def delete_chat_message(record_id: str) -> None:
    if not enabled() or not record_id:
        return
    try:
        feishu.client.delete_record(settings.feishu_table_chat_messages, record_id)
    except Exception as exc:  # noqa: BLE001
        if is_record_not_found_error(exc):
            return
        _warn_or_raise(f"飞书 AI 对话记录删除失败：{exc}")


def try_pull_chat_messages(conn, session_id: int) -> None:
    if not enabled():
        return
    try:
        session = conn.execute(
            "SELECT s.*, u.display_name, u.username FROM chat_sessions s LEFT JOIN users u ON u.id=s.user_id WHERE s.id=?",
            (session_id,),
        ).fetchone()
        if not session:
            return
        session_key = _chat_session_key(session["user_id"] or 0, session["run_ids_json"] or "[]")
        for record in _rows(settings.feishu_table_chat_messages):
            fields = record.get("fields") or {}
            if _pick(fields, "会话Key") != session_key:
                continue
            record_id = record.get("record_id") or ""
            local_id = _int(_pick(fields, "本地消息ID"), 0)
            role = _pick(fields, "角色")
            content = _pick(fields, "消息内容")
            if role not in ("user", "assistant") or not content:
                continue
            model = _pick(fields, "模型")
            tokens = _int(_pick(fields, "Tokens"), 0)
            if local_id:
                cur = conn.execute(
                    "UPDATE chat_messages SET role=?, content=?, model=?, tokens=?, feishu_record_id=? WHERE id=?",
                    (role, content, model, tokens, record_id, local_id),
                )
                if cur.rowcount:
                    continue
            existing = conn.execute(
                "SELECT id FROM chat_messages WHERE feishu_record_id=?",
                (record_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE chat_messages SET role=?, content=?, model=?, tokens=? WHERE id=?",
                    (role, content, model, tokens, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO chat_messages (session_id, role, content, model, tokens, feishu_record_id)"
                    " VALUES (?,?,?,?,?,?)",
                    (session_id, role, content, model, tokens, record_id),
                )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _warn_or_raise(f"飞书 AI 对话记录同步失败：{exc}")


def push_tracking(conn, tracking_id: int) -> None:
    if not enabled():
        return
    try:
        company_expr = company_display_expr(conn, "co")
        row = conn.execute(
            f"SELECT t.*, c.name AS candidate_name, p.title AS position_title, {company_expr} AS company"
            " FROM candidate_tracking t"
            " JOIN candidates c ON c.id=t.candidate_id"
            " LEFT JOIN positions p ON p.id=t.position_id"
            " LEFT JOIN companies co ON co.id=p.company_id"
            " WHERE t.id=?",
            (tracking_id,),
        ).fetchone()
        if not row:
            return
        data = dict(row)
        stage = _normalize_tracking_stage(data.get("stage") or "")
        fields = {
            "本地ID": data.get("id"),
            "进程名称": f"{data.get('candidate_name') or ''}+{data.get('position_title') or ''}".strip("+"),
            "进程阶段": stage,
            "当前状态": _derive_tracking_status(stage),
            "进程描述": data.get("client_feedback") or "",
            "跟进记录": data.get("reject_reason") or data.get("next_action") or "",
        }
        record_id = data.get("feishu_record_id") or ""
        if record_id:
            feishu.client.update_record(settings.feishu_table_tracking, record_id, fields)
        else:
            created = feishu.client.create_records(settings.feishu_table_tracking, [fields])
            items = created.get("records") or []
            new_id = (items[0] or {}).get("record_id") if items else ""
            if new_id:
                conn.execute("UPDATE candidate_tracking SET feishu_record_id=? WHERE id=?", (new_id, tracking_id))
                conn.commit()
    except Exception as exc:  # noqa: BLE001
        _warn_or_raise(f"飞书进程写入失败：{exc}")


def try_pull_tracking(conn) -> None:
    if not enabled():
        return
    try:
        for record in _rows(settings.feishu_table_tracking):
            fields = record.get("fields") or {}
            local_id = _int(_pick(fields, "本地ID", "local_id"), 0)
            record_id = record.get("record_id") or ""
            if not local_id and not record_id:
                continue
            stage = _pick(fields, "进程阶段", "当前阶段") or ""
            status = _pick(fields, "当前状态")
            if not stage and status == "已淘汰":
                stage = "pass"
            elif not stage and status == "已完成":
                stage = "入职"
            stage = _normalize_tracking_stage(stage)
            fields_sql = ["feishu_record_id=?"]
            params: list[Any] = [record_id]
            if stage:
                fields_sql.append("stage=?")
                params.append(stage)
            description = _pick(fields, "进程描述", "客户反馈")
            if description:
                fields_sql.append("client_feedback=?")
                params.append(description)
            note = _pick(fields, "跟进记录", "备注")
            if note:
                fields_sql.append("next_action=?")
                params.append(note)
            if local_id:
                params.append(local_id)
                conn.execute(
                    f"UPDATE candidate_tracking SET {', '.join(fields_sql)}, updated_at=datetime('now','localtime') WHERE id=?",
                    params,
                )
            else:
                params.append(record_id)
                conn.execute(
                    f"UPDATE candidate_tracking SET {', '.join(fields_sql)}, updated_at=datetime('now','localtime') WHERE feishu_record_id=?",
                    params,
                )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _warn_or_raise(f"飞书进程管理表同步失败：{exc}")


def _derive_tracking_status(stage: str) -> str:
    stage = _normalize_tracking_stage(stage)
    if stage in ("入职",):
        return "已完成"
    if stage == "pass":
        return "已淘汰"
    if stage in ("待推荐",):
        return "进行中"
    return "进行中"


def _normalize_tracking_stage(stage: str) -> str:
    aliases = {
        "已推荐客户": "初筛（已推荐客户）",
        "客户初筛通过": "初筛（已推荐客户）",
        "客户初筛未通过": "pass",
        "需补充信息": "初筛（已推荐客户）",
        "终面": "四面",
        "Offer": "offer",
        "OFFER": "offer",
        "淘汰": "pass",
        "Pass": "pass",
        "PASS": "pass",
    }
    value = (stage or "").strip()
    return aliases.get(value, value)


def delete_tracking(record_id: str) -> None:
    if not enabled() or not record_id:
        return
    try:
        feishu.client.delete_record(settings.feishu_table_tracking, record_id)
    except Exception as exc:  # noqa: BLE001
        if is_record_not_found_error(exc):
            return
        _warn_or_raise(f"飞书进程删除失败：{exc}")


def retry_failed_sync_jobs(conn, limit: int = 20) -> dict[str, Any]:
    handlers = {
        "company": push_company,
        "position": push_position,
        "prompt": push_prompt,
    }
    jobs = conn.execute(
        "SELECT * FROM feishu_sync_jobs"
        " WHERE status IN ('pending','failed') AND action='push'"
        " ORDER BY updated_at ASC LIMIT ?",
        (max(1, min(limit, 100)),),
    ).fetchall()
    retried = 0
    failed: list[dict[str, Any]] = []
    for job in jobs:
        handler = handlers.get(job["entity_type"])
        if not handler:
            failed.append({"id": job["id"], "error": f"未知同步类型：{job['entity_type']}"})
            continue
        try:
            handler(conn, job["entity_id"], commit_record_id=True)
            retried += 1
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            _mark_sync_job(conn, job["entity_type"], job["entity_id"], "failed", str(exc))
            conn.commit()
            failed.append({"id": job["id"], "error": str(exc)})
    return {"total": len(jobs), "retried": retried, "failed": failed}
