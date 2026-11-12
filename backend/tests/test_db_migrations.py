import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db


def test_migrate_allows_done_with_errors_status():
    old_schema = db.SCHEMA.replace(
        "CHECK (status IN ('pending','detailing','done','done_with_errors','failed'))",
        "CHECK (status IN ('pending','detailing','done','failed'))",
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(old_schema)
    conn.commit()

    db._migrate(conn)

    conn.execute(
        "INSERT INTO candidates (id, name, resume_text) VALUES (1, '候选人', '简历正文')"
    )
    conn.execute(
        "INSERT INTO match_runs (candidate_id, position_ids_json, status)"
        " VALUES (1, '[]', 'done_with_errors')"
    )
    conn.commit()
    row = conn.execute("SELECT status FROM match_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "done_with_errors"


def test_migrate_adds_chat_workspace_hidden_results_column():
    old_schema = db.SCHEMA.replace(
        "    hidden_result_ids_json TEXT NOT NULL DEFAULT '[]',\n",
        "",
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(old_schema)
    conn.commit()

    db._migrate(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
    assert "hidden_result_ids_json" in cols


def test_migrate_adds_chat_workspace_added_results_and_updated_at_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    conn.executescript(
        """
        DROP TABLE chat_messages;
        DROP TABLE chat_sessions;
        CREATE TABLE chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            run_ids_json TEXT NOT NULL DEFAULT '[]',
            hidden_result_ids_json TEXT NOT NULL DEFAULT '[]',
            title TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(user_id, run_ids_json)
        );
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
            role TEXT NOT NULL CHECK (role IN ('user','assistant')),
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        """
    )
    conn.commit()

    db._migrate(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
    assert "added_result_ids_json" in cols
    assert "updated_at" in cols


def test_migrate_promotes_configured_super_admin_names(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    conn.execute(
        "INSERT INTO users (username, password_hash, display_name, role)"
        " VALUES ('高贺雅姝', 'x', '高贺雅姝', 'consultant')"
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, display_name, role)"
        " VALUES ('顾问A', 'x', '顾问A', 'consultant')"
    )
    conn.commit()
    monkeypatch.setattr(db.settings, "super_admin_names", ["高贺雅姝"])

    db._migrate(conn)

    rows = {
        row["username"]: row["role"]
        for row in conn.execute("SELECT username, role FROM users ORDER BY id").fetchall()
    }
    assert rows == {"高贺雅姝": "super_admin", "顾问A": "consultant"}


def test_migrate_enforces_configured_business_admin_names(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    conn.execute(
        "INSERT INTO users (username, password_hash, display_name, role)"
        " VALUES ('lantao', 'x', 'lantao', 'consultant')"
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, display_name, role)"
        " VALUES ('旧管理员', 'x', '旧管理员', 'business_admin')"
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, display_name, role)"
        " VALUES ('高贺雅姝', 'x', '高贺雅姝', 'super_admin')"
    )
    conn.commit()
    monkeypatch.setattr(db.settings, "business_admin_names", ["lantao"])
    monkeypatch.setattr(db.settings, "super_admin_names", ["高贺雅姝"])

    db._migrate(conn)

    rows = {
        row["username"]: row["role"]
        for row in conn.execute("SELECT username, role FROM users ORDER BY id").fetchall()
    }
    assert rows == {"lantao": "business_admin", "旧管理员": "consultant", "高贺雅姝": "super_admin"}


def test_migrate_adds_company_short_name_and_legal_name_uniqueness():
    old_schema = db.SCHEMA.replace(
        "    legal_name TEXT DEFAULT '',\n    short_name TEXT DEFAULT '',\n",
        "",
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(old_schema)
    conn.execute("INSERT INTO companies (name) VALUES ('SUD')")
    conn.commit()

    db._migrate(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(companies)").fetchall()}
    assert "legal_name" in cols
    assert "short_name" in cols
    migrated = conn.execute(
        "SELECT name, legal_name, short_name FROM companies WHERE name='SUD'"
    ).fetchone()
    assert dict(migrated) == {"name": "SUD", "legal_name": "", "short_name": "SUD"}

    conn.execute(
        "INSERT INTO companies (name, legal_name) VALUES ('示例科技', '北京示例科技有限公司')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO companies (name, legal_name) VALUES ('另一简称', '北京示例科技有限公司')"
        )
    conn.execute("INSERT INTO companies (name, legal_name) VALUES ('另一空主体', '')")


def test_migrate_adds_position_fields_independently_when_project_status_exists():
    old_schema = db.SCHEMA
    for line in (
        "    n_talents INTEGER DEFAULT 0,\n",
        "    n_recommend INTEGER DEFAULT 0,\n",
        "    n_interview INTEGER DEFAULT 0,\n",
        "    n_offer INTEGER DEFAULT 0,\n",
        "    consultant TEXT DEFAULT '',\n",
        "    project_json TEXT DEFAULT '',\n",
    ):
        old_schema = old_schema.replace(line, "")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(old_schema)
    conn.commit()

    db._migrate(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
    assert {"project_status", "n_talents", "n_recommend", "n_interview", "n_offer", "consultant", "project_json"} <= cols
