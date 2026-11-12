import sqlite3
import threading
from typing import Optional

from ..config import settings
from ..db import get_conn
from . import matcher

_started = False
_lock = threading.Lock()
_wake_event = threading.Event()


def wake() -> None:
    _wake_event.set()


def _claim_next_run() -> Optional[int]:
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE match_runs SET status='pending', error='上次任务中断，已重新排队'"
            " WHERE status='detailing'"
            " AND finished_at IS NULL"
            " AND (started_at IS NULL OR started_at <= datetime('now','localtime', ?))",
            (f"-{settings.match_run_stale_minutes} minutes",),
        )
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM match_runs"
            " WHERE status='detailing'"
            " AND finished_at IS NULL"
            " AND started_at > datetime('now','localtime', ?)",
            (f"-{settings.match_run_stale_minutes} minutes",),
        ).fetchone()["c"]
        if active >= max(1, settings.match_global_concurrency):
            conn.commit()
            return None
        row = conn.execute(
            "SELECT id FROM match_runs WHERE status='pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            conn.commit()
            return None
        conn.execute(
            "UPDATE match_runs"
            " SET status='detailing', started_at=datetime('now','localtime'), attempts=attempts+1, error=''"
            " WHERE id=? AND status='pending'",
            (row["id"],),
        )
        conn.commit()
        return int(row["id"])
    except sqlite3.OperationalError:
        conn.rollback()
        return None
    except Exception:
        conn.rollback()
        raise


def _worker_loop() -> None:
    while True:
        run_id = _claim_next_run()
        if run_id:
            matcher.execute_run(run_id)
            continue
        _wake_event.wait(max(1, settings.match_queue_poll_seconds))
        _wake_event.clear()


def start_workers() -> None:
    global _started
    if not settings.match_queue_enabled:
        return
    with _lock:
        if _started:
            return
        for index in range(max(1, settings.match_queue_workers)):
            thread = threading.Thread(
                target=_worker_loop,
                name=f"match-queue-worker-{index + 1}",
                daemon=True,
            )
            thread.start()
        _started = True
    wake()
