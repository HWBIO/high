"""AI 成本类接口的用量记录和可选每日限额。"""
import json
from typing import Optional

from fastapi import HTTPException

from ..config import settings


def _quota_applies(user: dict) -> bool:
    return user.get("role") not in ("super_admin", "business_admin")


def check_daily_quota(conn, user: dict) -> None:
    if not _quota_applies(user):
        return
    request_limit = max(0, int(settings.ai_daily_request_limit or 0))
    token_limit = max(0, int(settings.ai_daily_token_limit or 0))
    if not request_limit and not token_limit:
        return
    row = conn.execute(
        "SELECT COUNT(*) AS requests, COALESCE(SUM(tokens), 0) AS tokens"
        " FROM ai_usage_logs"
        " WHERE user_id=? AND date(created_at)=date('now','localtime')",
        (user["id"],),
    ).fetchone()
    if request_limit and row["requests"] >= request_limit:
        raise HTTPException(429, "今日 AI 请求次数已达上限，请联系管理员")
    if token_limit and row["tokens"] >= token_limit:
        raise HTTPException(429, "今日 AI token 使用量已达上限，请联系管理员")


def record_usage(conn, user: dict, endpoint: str, model: str, tokens: int = 0, metadata: Optional[dict] = None) -> None:
    try:
        conn.execute(
            "INSERT INTO ai_usage_logs (user_id, endpoint, model, tokens, metadata_json)"
            " VALUES (?,?,?,?,?)",
            (
                user["id"],
                endpoint,
                model or "",
                max(0, int(tokens or 0)),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception:
        pass
