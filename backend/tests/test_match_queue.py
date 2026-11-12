import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services import match_queue


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE match_runs (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT DEFAULT '',
            started_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            finished_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO match_runs (id, status) VALUES (1, 'pending')")
    conn.execute("INSERT INTO match_runs (id, status) VALUES (2, 'pending')")
    conn.commit()
    return conn


def test_claim_next_run_respects_global_concurrency(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(match_queue, "get_conn", lambda: conn)
    monkeypatch.setattr(settings, "match_global_concurrency", 1)
    monkeypatch.setattr(settings, "match_run_stale_minutes", 120)

    assert match_queue._claim_next_run() == 1
    assert match_queue._claim_next_run() is None
    row = conn.execute("SELECT status, attempts FROM match_runs WHERE id=1").fetchone()
    assert row["status"] == "detailing"
    assert row["attempts"] == 1


def test_claim_next_run_requeues_stale_detailing(monkeypatch):
    conn = _conn()
    conn.execute(
        "UPDATE match_runs SET status='detailing', started_at=datetime('now','localtime','-3 hours') WHERE id=1"
    )
    conn.commit()
    monkeypatch.setattr(match_queue, "get_conn", lambda: conn)
    monkeypatch.setattr(settings, "match_global_concurrency", 1)
    monkeypatch.setattr(settings, "match_run_stale_minutes", 120)

    assert match_queue._claim_next_run() == 1
    row = conn.execute("SELECT status, attempts, error FROM match_runs WHERE id=1").fetchone()
    assert row["status"] == "detailing"
    assert row["attempts"] == 1
    assert row["error"] == ""
