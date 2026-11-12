"""统一清理匹配报告及其派生数据。"""
from typing import Optional

from ..config import settings
from ..db import loads_or
from . import feishu_store


def _delete_bound_feishu_record(table_id: str, record_id: str, label: str) -> None:
    if not record_id or not table_id or not settings.feishu_sync_enabled:
        return
    if not (settings.feishu_app_id and settings.feishu_app_secret and settings.feishu_base_token):
        return
    try:
        feishu_store.feishu.client.delete_record(table_id, record_id)
    except Exception as exc:  # noqa: BLE001
        if feishu_store.is_record_not_found_error(exc):
            return
        raise RuntimeError(f"飞书{label}删除失败：{exc}") from exc


def _scope_result_id(scope_json: str) -> Optional[int]:
    data = loads_or(scope_json or "[]", [])
    if isinstance(data, dict) and data.get("result_id"):
        return int(data["result_id"])
    return None


def _scope_run_ids(scope_json: str) -> list[int]:
    data = loads_or(scope_json or "[]", [])
    if isinstance(data, dict):
        return [int(run_id) for run_id in data.get("run_ids") or [] if run_id]
    if isinstance(data, list):
        return [int(run_id) for run_id in data if run_id]
    return []


def delete_match_result(conn, result_id: int) -> dict:
    """Delete one match result plus its client report and result-scoped chat data.

    The caller owns transaction commit/rollback so this can be reused inside
    position/company deletes.
    """
    result = conn.execute(
        "SELECT mr.id, mr.run_id, mr.feishu_record_id,"
        " c.name AS candidate_name, p.title AS position_title"
        " FROM match_results mr"
        " JOIN match_runs r ON r.id=mr.run_id"
        " JOIN candidates c ON c.id=r.candidate_id"
        " JOIN positions p ON p.id=mr.position_id"
        " WHERE mr.id=?",
        (result_id,),
    ).fetchone()
    if not result:
        raise ValueError("匹配报告不存在")

    client_report_rows = conn.execute(
        "SELECT id, feishu_record_id FROM client_recommendation_reports WHERE result_id=?",
        (result_id,),
    ).fetchall()
    chat_session_rows = conn.execute(
        "SELECT id, run_ids_json FROM chat_sessions",
    ).fetchall()
    scoped_session_ids = [
        row["id"] for row in chat_session_rows
        if _scope_result_id(row["run_ids_json"] or "[]") == result_id
        or (
            _scope_result_id(row["run_ids_json"] or "[]") is None
            and result["run_id"] in _scope_run_ids(row["run_ids_json"] or "[]")
        )
    ]
    chat_message_rows = []
    if scoped_session_ids:
        placeholders = ",".join(["?"] * len(scoped_session_ids))
        chat_message_rows = conn.execute(
            "SELECT feishu_record_id FROM chat_messages"
            f" WHERE session_id IN ({placeholders}) AND feishu_record_id != ''",
            scoped_session_ids,
        ).fetchall()

    for row in client_report_rows:
        _delete_bound_feishu_record(
            settings.feishu_table_client_recommendations,
            row["feishu_record_id"] or "",
            "客户推荐报告",
        )
    _delete_bound_feishu_record(
        settings.feishu_table_match_results,
        result["feishu_record_id"] or "",
        "匹配报告",
    )
    for row in chat_message_rows:
        _delete_bound_feishu_record(
            settings.feishu_table_chat_messages,
            row["feishu_record_id"] or "",
            "AI 对话记录",
        )

    conn.execute("DELETE FROM client_recommendation_reports WHERE result_id=?", (result_id,))
    if scoped_session_ids:
        placeholders = ",".join(["?"] * len(scoped_session_ids))
        conn.execute(f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})", scoped_session_ids)
        conn.execute(f"DELETE FROM chat_sessions WHERE id IN ({placeholders})", scoped_session_ids)
    conn.execute("DELETE FROM match_results WHERE id=?", (result_id,))
    return {
        "ok": True,
        "result_id": result_id,
        "title": f"{result['candidate_name']}+{result['position_title']}",
    }
