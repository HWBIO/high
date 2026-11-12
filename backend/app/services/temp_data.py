import time
from pathlib import Path
from typing import Optional

from ..config import settings


def _referenced_resume_files(conn) -> set[str]:
    rows = conn.execute("SELECT resume_file FROM candidates WHERE resume_file != ''").fetchall()
    return {str(Path(row["resume_file"]).resolve()) for row in rows if row["resume_file"]}


def _upload_files() -> list[Path]:
    if not settings.upload_dir.exists():
        return []
    return [path for path in settings.upload_dir.iterdir() if path.is_file()]


def cleanup_expired_temp_resume_files(conn, retention_days: Optional[int] = None) -> int:
    """Delete expired temp resume originals."""
    days = retention_days if retention_days is not None else settings.temp_resume_file_retention_days
    if days <= 0:
        return 0
    rows = conn.execute(
        "SELECT id, resume_file FROM candidates"
        " WHERE is_temp=1 AND resume_file != ''"
        " AND created_at <= datetime('now','localtime', ?)",
        (f"-{days} days",),
    ).fetchall()
    cleaned_ids = []
    for row in rows:
        path = Path(row["resume_file"])
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            continue
        cleaned_ids.append(row["id"])
    if cleaned_ids:
        placeholders = ",".join(["?"] * len(cleaned_ids))
        conn.execute(
            f"UPDATE candidates SET resume_file='' WHERE id IN ({placeholders})",
            cleaned_ids,
        )
        conn.commit()
    return len(cleaned_ids)


def _active_temp_run_filter() -> str:
    return (
        "NOT EXISTS ("
        " SELECT 1 FROM match_runs r"
        " WHERE r.candidate_id=candidates.id"
        " AND r.status IN ('pending','detailing')"
        ")"
    )


def temp_resume_stats(conn, retention_days: Optional[int] = None) -> dict:
    days = retention_days if retention_days is not None else settings.temp_resume_text_retention_days
    active_filter = _active_temp_run_filter()
    total = conn.execute("SELECT COUNT(*) AS c FROM candidates WHERE is_temp=1").fetchone()["c"]
    with_file = conn.execute(
        "SELECT COUNT(*) AS c FROM candidates WHERE is_temp=1 AND resume_file != ''"
    ).fetchone()["c"]
    with_text = conn.execute(
        "SELECT COUNT(*) AS c FROM candidates"
        " WHERE is_temp=1 AND (resume_text != '' OR resume_struct_json != '')"
    ).fetchone()["c"]
    eligible = 0
    if days > 0:
        eligible = conn.execute(
            "SELECT COUNT(*) AS c FROM candidates"
            " WHERE is_temp=1"
            " AND created_at <= datetime('now','localtime', ?)"
            " AND (resume_file != '' OR resume_text != '' OR resume_struct_json != '')"
            f" AND {active_filter}",
            (f"-{days} days",),
        ).fetchone()["c"]
    referenced = _referenced_resume_files(conn)
    cutoff = time.time() - max(days, 0) * 86400
    upload_files = _upload_files()
    orphan_files = [path for path in upload_files if str(path.resolve()) not in referenced]
    expired_upload_files = [
        path for path in upload_files
        if days > 0 and path.stat().st_mtime <= cutoff
    ]
    return {
        "retention_days": days,
        "temp_candidates": total,
        "with_resume_file": with_file,
        "with_resume_text": with_text,
        "eligible_for_cleanup": eligible,
        "upload_files": len(upload_files),
        "orphan_upload_files": len(orphan_files),
        "expired_upload_files": len(expired_upload_files),
    }


def cleanup_orphan_upload_files(conn, retention_days: Optional[int] = None) -> dict:
    """Delete unreferenced upload files older than retention_days."""
    days = retention_days if retention_days is not None else settings.temp_resume_file_retention_days
    if days <= 0:
        return {"retention_days": days, "orphan_upload_files_deleted": 0}
    referenced = _referenced_resume_files(conn)
    cutoff = time.time() - days * 86400
    deleted = 0
    for path in _upload_files():
        try:
            if str(path.resolve()) in referenced or path.stat().st_mtime > cutoff:
                continue
            path.unlink()
            deleted += 1
        except OSError:
            continue
    return {"retention_days": days, "orphan_upload_files_deleted": deleted}


def cleanup_expired_temp_resume_payloads(conn, retention_days: Optional[int] = None) -> dict:
    """Clear expired temp resume originals and parsed text without deleting reports."""
    days = retention_days if retention_days is not None else settings.temp_resume_text_retention_days
    if days <= 0:
        return {
            "retention_days": days,
            "temp_candidates_cleaned": 0,
            "resume_files_deleted": 0,
        }
    active_filter = _active_temp_run_filter()
    rows = conn.execute(
        "SELECT id, resume_file FROM candidates"
        " WHERE is_temp=1"
        " AND created_at <= datetime('now','localtime', ?)"
        " AND (resume_file != '' OR resume_text != '' OR resume_struct_json != '')"
        f" AND {active_filter}",
        (f"-{days} days",),
    ).fetchall()
    cleaned_ids = []
    files_deleted = 0
    for row in rows:
        resume_file = row["resume_file"] or ""
        if resume_file:
            path = Path(resume_file)
            try:
                if path.exists() and path.is_file():
                    path.unlink()
                    files_deleted += 1
            except OSError:
                continue
        cleaned_ids.append(row["id"])
    if cleaned_ids:
        placeholders = ",".join(["?"] * len(cleaned_ids))
        conn.execute(
            "UPDATE candidates"
            " SET resume_file='', resume_text='', resume_struct_json=''"
            f" WHERE id IN ({placeholders})",
            cleaned_ids,
        )
        conn.commit()
    return {
        "retention_days": days,
        "temp_candidates_cleaned": len(cleaned_ids),
        "resume_files_deleted": files_deleted,
    }
