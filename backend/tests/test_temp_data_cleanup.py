import os
import sqlite3
import time

from app.services import temp_data


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            is_temp INTEGER,
            resume_file TEXT,
            resume_text TEXT,
            resume_struct_json TEXT,
            created_at TEXT
        );
        CREATE TABLE match_runs (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER,
            status TEXT
        );
        """
    )
    return conn


def test_cleanup_expired_temp_resume_payloads_keeps_active_runs():
    conn = _conn()
    conn.execute(
        "INSERT INTO candidates VALUES (1, 1, '/missing/a.pdf', '简历A', '{}', datetime('now','localtime','-40 days'))"
    )
    conn.execute(
        "INSERT INTO candidates VALUES (2, 1, '/missing/b.pdf', '简历B', '{}', datetime('now','localtime','-40 days'))"
    )
    conn.execute("INSERT INTO match_runs VALUES (100, 2, 'detailing')")
    conn.commit()

    result = temp_data.cleanup_expired_temp_resume_payloads(conn, retention_days=30)

    assert result["temp_candidates_cleaned"] == 1
    cleaned = conn.execute("SELECT resume_file, resume_text, resume_struct_json FROM candidates WHERE id=1").fetchone()
    active = conn.execute("SELECT resume_file, resume_text, resume_struct_json FROM candidates WHERE id=2").fetchone()
    assert dict(cleaned) == {"resume_file": "", "resume_text": "", "resume_struct_json": ""}
    assert active["resume_text"] == "简历B"


def test_temp_resume_stats_counts_cleanup_eligible_rows():
    conn = _conn()
    conn.execute(
        "INSERT INTO candidates VALUES (1, 1, '/missing/a.pdf', '简历A', '{}', datetime('now','localtime','-40 days'))"
    )
    conn.execute(
        "INSERT INTO candidates VALUES (2, 1, '', '', '', datetime('now','localtime','-40 days'))"
    )
    conn.execute(
        "INSERT INTO candidates VALUES (3, 0, '/missing/c.pdf', '简历C', '{}', datetime('now','localtime','-40 days'))"
    )
    conn.commit()

    stats = temp_data.temp_resume_stats(conn, retention_days=30)

    assert stats["temp_candidates"] == 2
    assert stats["with_resume_file"] == 1
    assert stats["with_resume_text"] == 1
    assert stats["eligible_for_cleanup"] == 1


def test_cleanup_orphan_upload_files_keeps_referenced_and_recent_files(tmp_path, monkeypatch):
    conn = _conn()
    referenced = tmp_path / "referenced.pdf"
    old_orphan = tmp_path / "old_orphan.pdf"
    recent_orphan = tmp_path / "recent_orphan.pdf"
    referenced.write_text("ref", encoding="utf-8")
    old_orphan.write_text("old", encoding="utf-8")
    recent_orphan.write_text("new", encoding="utf-8")
    old_ts = time.time() - 40 * 86400
    old_orphan.touch()
    referenced.touch()
    recent_orphan.touch()
    os.utime(old_orphan, (old_ts, old_ts))
    os.utime(referenced, (old_ts, old_ts))
    conn.execute(
        "INSERT INTO candidates VALUES (1, 1, ?, '简历A', '{}', datetime('now','localtime','-40 days'))",
        (str(referenced),),
    )
    conn.commit()
    monkeypatch.setattr(temp_data.settings, "upload_dir", tmp_path)

    result = temp_data.cleanup_orphan_upload_files(conn, retention_days=30)
    stats = temp_data.temp_resume_stats(conn, retention_days=30)

    assert result["orphan_upload_files_deleted"] == 1
    assert referenced.exists()
    assert not old_orphan.exists()
    assert recent_orphan.exists()
    assert stats["orphan_upload_files"] == 1
