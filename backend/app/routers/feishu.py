from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_conn
from ..services import feishu
from ..services import feishu_store
from .auth import require_business

router = APIRouter(prefix="/api/feishu", tags=["feishu"])


@router.get("/status")
def get_feishu_status(_user=Depends(require_business)):
    """Return Feishu config readiness without exposing secrets."""
    status = feishu.config_status()
    conn = get_conn()
    failed_count = conn.execute(
        "SELECT COUNT(*) AS c FROM feishu_sync_jobs WHERE status IN ('pending','failed')"
    ).fetchone()["c"]
    return {**status, "failed_sync_jobs": failed_count}


@router.get("/sync-jobs")
def list_sync_jobs(status: str = "failed", limit: int = Query(default=50, ge=1, le=200), _user=Depends(require_business)):
    conn = get_conn()
    where = "WHERE status=?" if status else ""
    params = [status] if status else []
    rows = conn.execute(
        f"SELECT * FROM feishu_sync_jobs {where} ORDER BY updated_at DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [dict(row) for row in rows]


@router.post("/sync-jobs/retry")
def retry_sync_jobs(limit: int = Query(default=20, ge=1, le=100), _user=Depends(require_business)):
    conn = get_conn()
    return feishu_store.retry_failed_sync_jobs(conn, limit=limit)


@router.post("/pull")
def pull_feishu_storage(table: str = Query(default="all"), _user=Depends(require_business)):
    """手动从飞书拉取数据到本地 SQLite。页面读取不会自动触发此操作。"""
    pullers = {
        "users": [feishu_store.try_pull_users],
        "companies": [feishu_store.try_pull_users, feishu_store.try_pull_companies],
        "positions": [feishu_store.try_pull_positions],
        "prompts": [feishu_store.try_pull_positions, feishu_store.try_pull_prompts],
        "match_results": [feishu_store.try_pull_match_results],
        "client_recommendations": [feishu_store.try_pull_client_recommendations],
        "tracking": [
            feishu_store.try_pull_users,
            feishu_store.try_pull_positions,
            feishu_store.try_pull_tracking,
        ],
    }
    all_pullers = [
        feishu_store.try_pull_users,
        feishu_store.try_pull_companies,
        feishu_store.try_pull_positions,
        feishu_store.try_pull_prompts,
        feishu_store.try_pull_match_results,
        feishu_store.try_pull_client_recommendations,
        feishu_store.try_pull_tracking,
    ]
    selected = all_pullers if table == "all" else pullers.get(table)
    if not selected:
        raise HTTPException(status_code=400, detail=f"未知同步范围：{table}")
    conn = get_conn()
    for puller in selected:
        puller(conn)
    return {"pulled": table, "count": len(selected)}


@router.get("/tables/{table_name}/records")
def list_feishu_records(
    table_name: str,
    page_size: int = Query(default=20, ge=1, le=200),
    page_token: str = "",
    _user=Depends(require_business),
):
    tables = feishu.table_config().as_dict()
    table_id = tables.get(table_name)
    if not table_id:
        raise HTTPException(status_code=404, detail=f"未知飞书表：{table_name}")
    try:
        return feishu.client.list_records(table_id, page_size=page_size, page_token=page_token)
    except feishu.FeishuConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except feishu.FeishuAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
