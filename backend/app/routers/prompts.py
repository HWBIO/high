import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import company_display_expr, get_conn
from ..services import feishu_store, jd_extractor, job_label_dictionary, prompt_composer, scoring
from ..services.positions_helpers import ensure_company
from .auth import require_prompt_operator, require_user

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

VALID_LAYERS = ("general", "company", "position")


@router.get("/weights")
def get_weights():
    """真实生效的计分规则。当前不再提供四维权重配置。"""
    return scoring.rule_info(
        review_threshold=settings.review_threshold,
        top_threshold=settings.top_threshold,
    )


class PromptIn(BaseModel):
    layer: str
    content: str
    scope: str = "company_default"
    company: str = ""       # layer=company 时用公司名定位/创建
    company_id: int = None   # layer=company 时也可直接传公司 id
    position_id: int = None  # layer=position 时必填
    updated_by: str = ""


class PromptUpdate(BaseModel):
    content: str = None
    version: int = None


def _user_value(user, key: str):
    if hasattr(user, "get"):
        return user.get(key)
    try:
        return user[key]
    except (KeyError, TypeError, IndexError):
        return None


def _user_id(user) -> int:
    return int(_user_value(user, "id") or 0)


def _user_name(user) -> str:
    return _user_value(user, "display_name") or _user_value(user, "username") or ""


def _can_manage_company_default(user) -> bool:
    return _user_value(user, "role") in ("super_admin", "business_admin")


def _effective_general_prompt_id(conn, user_id: int):
    pref = conn.execute(
        "SELECT general_prompt_id FROM user_prompt_preferences WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if pref and pref["general_prompt_id"]:
        row = conn.execute(
            "SELECT id FROM prompts WHERE id=? AND layer='general'",
            (pref["general_prompt_id"],),
        ).fetchone()
        if row:
            return row["id"]
    row = conn.execute(
        "SELECT id FROM prompts WHERE layer='general' AND scope='company_default'"
        " ORDER BY is_active DESC, version DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def _set_general_preference(conn, user_id: int, prompt_id: int, source: str) -> None:
    conn.execute(
        "INSERT INTO user_prompt_preferences"
        " (user_id, general_prompt_id, general_prompt_source, updated_at)"
        " VALUES (?,?,?,datetime('now','localtime'))"
        " ON CONFLICT(user_id) DO UPDATE SET"
        " general_prompt_id=excluded.general_prompt_id,"
        " general_prompt_source=excluded.general_prompt_source,"
        " updated_at=datetime('now','localtime')",
        (user_id, prompt_id, source),
    )


@router.get("")
def list_prompts(layer: str = "", include_history: bool = False, user=Depends(require_user)):
    conn = get_conn()
    company_expr = company_display_expr(conn, "c")
    conditions, params = [], []
    if layer in VALID_LAYERS:
        conditions.append("pr.layer=?")
        params.append(layer)
    conditions.append("(pr.scope!='personal' OR pr.owner_user_id=?)")
    params.append(_user_id(user))
    if not include_history:
        if layer == "general":
            conditions.append("(pr.is_active=1 OR pr.id=?)")
            params.append(_effective_general_prompt_id(conn, _user_id(user)) or 0)
        else:
            conditions.append("pr.is_active=1")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    effective_general_id = _effective_general_prompt_id(conn, _user_id(user))
    rows = conn.execute(
        "SELECT pr.id, pr.layer, pr.company_id, pr.position_id, pr.content,"
        " pr.is_active, pr.version, pr.updated_by, pr.scope, pr.owner_user_id,"
        " pr.owner_name, pr.created_at,"
        f" {company_expr} AS company, p.title AS position_title"
        " FROM prompts pr"
        " LEFT JOIN companies c ON c.id=pr.company_id"
        " LEFT JOIN positions p ON p.id=pr.position_id"
        f" {where} ORDER BY pr.layer, pr.company_id, pr.position_id, pr.version DESC",
        params,
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["is_effective"] = 1 if item["layer"] == "general" and item["id"] == effective_general_id else 0
        items.append(item)
    return items


@router.post("")
def save_prompt(body: PromptIn, _user=Depends(require_prompt_operator)):
    """保存即新版本：同 scope 旧 active 版本失活，插入 version+1。"""
    if body.layer not in VALID_LAYERS:
        raise HTTPException(400, f"layer 必须是 {VALID_LAYERS} 之一")
    if not body.content.strip():
        raise HTTPException(400, "content 不能为空")
    conn = get_conn()
    try:
        company_id, position_id = None, None
        scope = "company_default"
        owner_user_id = None
        owner_name = ""
        if body.layer == "company":
            if body.company_id:
                row = conn.execute("SELECT id FROM companies WHERE id=?", (body.company_id,)).fetchone()
                if not row:
                    raise HTTPException(404, "公司不存在")
                company_id = body.company_id
            else:
                if not body.company.strip():
                    raise HTTPException(400, "公司提示词需要提供 company 或 company_id")
                company_id = ensure_company(conn, body.company)
        elif body.layer == "position":
            if not body.position_id:
                raise HTTPException(400, "岗位提示词需要提供 position_id")
            row = conn.execute(
                "SELECT id FROM positions WHERE id=?", (body.position_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "岗位不存在")
            position_id = body.position_id
        elif body.layer == "general":
            scope = body.scope if body.scope in ("company_default", "personal") else "company_default"
            if scope == "company_default" and not _can_manage_company_default(_user):
                raise HTTPException(403, "只有管理员可以保存公司通用提示词")
            if scope == "personal":
                owner_user_id = _user_id(_user)
                owner_name = _user_name(_user)

        scope_cond = "layer=? AND company_id IS ? AND position_id IS ? AND scope=? AND owner_user_id IS ?"
        scope_params = (body.layer, company_id, position_id, scope, owner_user_id)
        old = conn.execute(
            f"SELECT MAX(version) AS v FROM prompts WHERE {scope_cond}", scope_params
        ).fetchone()
        next_version = (old["v"] or 0) + 1
        conn.execute(
            f"UPDATE prompts SET is_active=0 WHERE {scope_cond} AND is_active=1",
            scope_params,
        )
        cur = conn.execute(
            "INSERT INTO prompts"
            " (layer, company_id, position_id, content, version, updated_by, scope, owner_user_id, owner_name)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                body.layer,
                company_id,
                position_id,
                body.content,
                next_version,
                body.updated_by or _user_name(_user),
                scope,
                owner_user_id,
                owner_name,
            ),
        )
        if body.layer == "general":
            _set_general_preference(
                conn,
                _user_id(_user),
                cur.lastrowid,
                "personal" if scope == "personal" else "company",
            )
        if body.layer == "company" and company_id:
            feishu_store.push_company(conn, company_id, commit_record_id=False)
        if scope != "personal":
            feishu_store.push_prompt(conn, cur.lastrowid, commit_record_id=False)
        conn.commit()
        return {"id": cur.lastrowid, "version": next_version}
    except Exception:
        conn.rollback()
        raise


@router.put("/{prompt_id}")
def update_prompt(prompt_id: int, body: PromptUpdate, _user=Depends(require_prompt_operator)):
    """行内编辑提示词历史记录：只改内容/版本，不改变生效状态。"""
    conn = get_conn()
    row = conn.execute("SELECT id, layer, scope, owner_user_id FROM prompts WHERE id=?", (prompt_id,)).fetchone()
    if not row:
        raise HTTPException(404, "提示词不存在")
    if row["scope"] == "personal" and row["owner_user_id"] != _user_id(_user):
        raise HTTPException(403, "只能编辑自己的个人通用提示词")
    if row["layer"] == "general" and row["scope"] == "company_default" and not _can_manage_company_default(_user):
        raise HTTPException(403, "只有管理员可以编辑公司通用提示词")
    fields, params = [], []
    if body.content is not None:
        if not body.content.strip():
            raise HTTPException(400, "content 不能为空")
        fields.append("content=?")
        params.append(body.content.strip())
    if body.version is not None:
        if body.version < 1:
            raise HTTPException(400, "version 必须大于等于 1")
        fields.append("version=?")
        params.append(body.version)
    if not fields:
        return {"updated": False}
    params.append(prompt_id)
    try:
        conn.execute(f"UPDATE prompts SET {', '.join(fields)} WHERE id=?", params)
        if row["scope"] != "personal":
            feishu_store.push_prompt(conn, prompt_id, commit_record_id=False)
        conn.commit()
        return {"updated": True}
    except Exception:
        conn.rollback()
        raise


@router.post("/{prompt_id}/activate")
def activate_prompt(prompt_id: int, _user=Depends(require_prompt_operator)):
    """把已有版本设为当前生效版本：同 scope 其他版本失活。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, layer, company_id, position_id, scope, owner_user_id FROM prompts WHERE id=?",
        (prompt_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "提示词不存在")
    if row["layer"] == "general":
        if row["scope"] == "personal" and row["owner_user_id"] != _user_id(_user):
            raise HTTPException(403, "只能应用自己的个人通用提示词")
        try:
            if row["scope"] == "personal":
                conn.execute(
                    "UPDATE prompts SET is_active=0 WHERE layer='general' AND scope='personal' AND owner_user_id=?",
                    (_user_id(_user),),
                )
                conn.execute("UPDATE prompts SET is_active=1 WHERE id=?", (prompt_id,))
                _set_general_preference(conn, _user_id(_user), prompt_id, "personal")
            else:
                if _can_manage_company_default(_user):
                    old_active_rows = conn.execute(
                        "SELECT id FROM prompts WHERE layer='general' AND scope='company_default'"
                        " AND is_active=1 AND id<>?",
                        (prompt_id,),
                    ).fetchall()
                    conn.execute(
                        "UPDATE prompts SET is_active=0 WHERE layer='general' AND scope='company_default' AND is_active=1"
                    )
                    conn.execute("UPDATE prompts SET is_active=1 WHERE id=?", (prompt_id,))
                    for old_row in old_active_rows:
                        feishu_store.push_prompt(conn, old_row["id"], commit_record_id=False)
                    feishu_store.push_prompt(conn, prompt_id, commit_record_id=False)
                _set_general_preference(conn, _user_id(_user), prompt_id, "company")
            conn.commit()
            return {"activated": True}
        except Exception:
            conn.rollback()
            raise

    scope_cond = "layer=? AND company_id IS ? AND position_id IS ?"
    scope_params = (row["layer"], row["company_id"], row["position_id"])
    old_active_rows = conn.execute(
        f"SELECT id FROM prompts WHERE {scope_cond} AND is_active=1 AND id<>?",
        (*scope_params, prompt_id),
    ).fetchall()
    try:
        conn.execute(
            f"UPDATE prompts SET is_active=0 WHERE {scope_cond} AND is_active=1",
            scope_params,
        )
        conn.execute("UPDATE prompts SET is_active=1 WHERE id=?", (prompt_id,))

        for old_row in old_active_rows:
            feishu_store.push_prompt(conn, old_row["id"], commit_record_id=False)
        feishu_store.push_prompt(conn, prompt_id, commit_record_id=False)
        conn.commit()
        return {"activated": True}
    except Exception:
        conn.rollback()
        raise


def _compose_preview_payload(conn, row, jd_struct: dict, user_id: int = None) -> dict:
    pos = dict(row)
    pos["jd_struct"] = jd_struct
    general = prompt_composer.resolve_active_prompt(conn, "general", user_id=user_id)
    company_prompt = prompt_composer.resolve_active_prompt(
        conn, "company", company_id=pos.get("company_id")
    )
    position_prompt = prompt_composer.resolve_active_prompt(
        conn, "position", position_id=pos["id"]
    )
    label_context = job_label_dictionary.compose_label_block(
        pos,
        company_prompt,
        position_prompt,
    )
    return {
        "system": prompt_composer.compose_system(general, "detail"),
        "user": prompt_composer.compose_detail_user(
            pos,
            "（此处将插入候选人简历全文）",
            company_prompt,
            position_prompt,
            label_context=label_context,
        ),
        "layers": {
            "general_present": bool(general),
            "company_present": bool(company_prompt),
            "position_present": bool(position_prompt),
        },
    }


def _position_for_preview(conn, position_id: int):
    company_expr = company_display_expr(conn, "c")
    row = conn.execute(
        f"SELECT p.*, {company_expr} AS company_name FROM positions p"
        " LEFT JOIN companies c ON c.id=p.company_id WHERE p.id=?",
        (position_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "岗位不存在")
    return row


@router.get("/compose-preview")
def compose_preview(position_id: int, user=Depends(require_user)):
    """只读返回已缓存的完整 prompt；不会触发 JD 结构化或写库。"""
    conn = get_conn()
    row = _position_for_preview(conn, position_id)
    current_hash = jd_extractor.jd_hash(row["jd_raw"], row["consultant_notes"] or "")
    if row["jd_hash"] != current_hash or not row["jd_struct_json"]:
        raise HTTPException(409, "岗位 JD 结构化缓存尚未生成，请使用 POST 生成预览")
    try:
        jd_struct = json.loads(row["jd_struct_json"])
    except json.JSONDecodeError as exc:
        raise HTTPException(409, "岗位 JD 结构化缓存不可用，请使用 POST 重新生成预览") from exc
    return _compose_preview_payload(conn, row, jd_struct, _user_id(user))


@router.post("/compose-preview")
def generate_compose_preview(position_id: int, _user=Depends(require_prompt_operator)):
    """显式生成/刷新 JD 结构化缓存后返回完整 prompt。"""
    conn = get_conn()
    row = _position_for_preview(conn, position_id)
    return _compose_preview_payload(conn, row, jd_extractor.ensure_struct(conn, row), _user_id(_user))
