"""账号体系：注册 / 登录 / 会话。

密码用 PBKDF2-SHA256（stdlib）哈希；会话 token 存 sessions 表，有效期 30 天。
业务接口在 main.py 按 router 统一要求登录，并在具体写操作上叠加角色权限。
"""
import hashlib
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..db import get_conn
from ..services import feishu_store, operation_log

router = APIRouter(prefix="/api/auth", tags=["auth"])

PBKDF2_ITERATIONS = 100_000
SESSION_DAYS = 30
PROTECTED_SUPER_ADMIN_NAMES = {"高贺雅姝"}


def _hash_password(password: str, salt: str = "") -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()
    return f"{salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    salt = stored.split("$", 1)[0]
    return secrets.compare_digest(_hash_password(password, salt), stored)


def _create_session(conn, user_id: int) -> str:
    token = secrets.token_hex(32)
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at)"
        " VALUES (?, ?, datetime('now', 'localtime', ?))",
        (token, user_id, f"+{SESSION_DAYS} days"),
    )
    conn.commit()
    return token


def _user_public(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _is_protected_super_admin(row) -> bool:
    return (
        row["role"] == "super_admin"
        and ((row["username"] or "") in PROTECTED_SUPER_ADMIN_NAMES or (row["display_name"] or "") in PROTECTED_SUPER_ADMIN_NAMES)
    )


def current_user(conn, authorization: str):
    """从 Authorization: Bearer <token> 解析当前用户，无效返回 None。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token=? AND s.expires_at > datetime('now', 'localtime')",
        (token,),
    ).fetchone()
    return row


def require_user(authorization: str = Header(default="")):
    conn = get_conn()
    user = current_user(conn, authorization)
    if not user:
        raise HTTPException(401, "未登录或会话已过期")
    if user["status"] == "disabled":
        raise HTTPException(403, "账号已停用，请联系管理员")
    if user["role"] not in ("super_admin", "business_admin", "consultant"):
        raise HTTPException(403, "账号角色已停用，请联系管理员")
    return user


def require_roles(*roles: str):
    allowed = set(roles)

    def dependency(user=Depends(require_user)):
        if user["role"] == "super_admin" or user["role"] in allowed:
            return user
        raise HTTPException(403, "当前账号无权限执行该操作")

    return dependency


def require_business(user=Depends(require_roles("business_admin"))):
    return user


def require_prompt_operator(user=Depends(require_roles("business_admin", "consultant"))):
    return user


def require_position_operator(user=Depends(require_roles("business_admin", "consultant"))):
    return user


def require_match_operator(user=Depends(require_roles("business_admin", "consultant"))):
    return user


def require_report_reader(user=Depends(require_roles("business_admin", "consultant"))):
    return user


def require_ai_operator(user=Depends(require_roles("business_admin", "consultant"))):
    return user


def require_client_report_operator(user=Depends(require_roles("business_admin", "consultant"))):
    return user


def require_process_operator(user=Depends(require_roles("business_admin", "consultant"))):
    return user


class RegisterIn(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class MemberUpdate(BaseModel):
    role: str = ""
    status: str = ""


@router.post("/register")
def register(body: RegisterIn):
    username = body.username.strip()
    if len(username) < 2:
        raise HTTPException(400, "姓名/顾问名至少 2 个字符")
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    conn = get_conn()
    if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        raise HTTPException(409, "姓名/顾问名已存在")
    display_name = username
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, display_name) VALUES (?,?,?)",
        (username, _hash_password(body.password), display_name),
    )
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    feishu_store.push_user(conn, user["id"])
    return {"token": _create_session(conn, user["id"]), "user": _user_public(user)}


@router.post("/login")
def login(body: LoginIn):
    conn = get_conn()
    user = conn.execute(
        "SELECT * FROM users WHERE username=?", (body.username.strip(),)
    ).fetchone()
    if not user or not _verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "姓名/顾问名或密码错误")
    if user["status"] == "disabled":
        raise HTTPException(403, "账号已停用，请联系管理员")
    return {"token": _create_session(conn, user["id"]), "user": _user_public(user)}


@router.get("/me")
def me(user=Depends(require_user)):
    return _user_public(user)


@router.post("/logout")
def logout(authorization: str = Header(default="")):
    if authorization.startswith("Bearer "):
        conn = get_conn()
        conn.execute("DELETE FROM sessions WHERE token=?", (authorization[7:].strip(),))
        conn.commit()
    return {"loggedOut": True}


@router.get("/users")
def list_users(_user=Depends(require_user)):
    """可分配业务用户（用于「联系人/顾问」下拉）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, username, display_name, role, status FROM users"
        " WHERE status='active' AND role IN ('business_admin','consultant')"
        " ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/admin/users")
def list_admin_users(_user=Depends(require_business)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, username, display_name, role, status, created_at FROM users"
        " ORDER BY id"
    ).fetchall()
    if not _is_protected_super_admin(_user):
        rows = [row for row in rows if not _is_protected_super_admin(row)]
    return {"items": [dict(r) for r in rows]}


@router.put("/admin/users/{user_id}")
def update_admin_user(user_id: int, body: MemberUpdate, actor=Depends(require_business)):
    conn = get_conn()
    target = conn.execute(
        "SELECT id, username, display_name, role, status FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    if not target:
        raise HTTPException(404, "用户不存在")
    if _is_protected_super_admin(target) and not _is_protected_super_admin(actor):
        raise HTTPException(404, "用户不存在")
    if target["role"] == "super_admin" and actor["role"] != "super_admin":
        raise HTTPException(403, "super_admin 账号只能由技术管理员调整")

    fields = []
    params = []
    detail = {}
    role = body.role.strip()
    status = body.status.strip()
    if role:
        allowed_roles = {"business_admin", "consultant"}
        if actor["role"] == "super_admin":
            allowed_roles.add("super_admin")
        if role not in allowed_roles:
            raise HTTPException(400, "角色只能设置为管理员或顾问")
        if role != target["role"]:
            fields.append("role=?")
            params.append(role)
            detail["role"] = {"old": target["role"], "new": role}
    if status:
        if status not in ("active", "disabled"):
            raise HTTPException(400, "状态只能设置为启用或停用")
        if int(actor["id"]) == int(user_id) and status == "disabled":
            raise HTTPException(400, "不能停用当前登录账号")
        if status != target["status"]:
            fields.append("status=?")
            params.append(status)
            detail["status"] = {"old": target["status"], "new": status}
    if fields:
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", (*params, user_id))
        operation_log.record(
            conn,
            actor,
            "update",
            "user",
            user_id,
            target["display_name"] or target["username"] or "",
            detail,
        )
        conn.commit()
        feishu_store.push_user(conn, user_id)
    row = conn.execute(
        "SELECT id, username, display_name, role, status, created_at FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    return dict(row)
