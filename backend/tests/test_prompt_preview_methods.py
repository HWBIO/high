import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SCHEMA
from app.routers import prompts
from app.services import jd_extractor


def _conn(cached: bool = False):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO companies (id, name) VALUES (1, '客户A')")
    jd_raw = "负责 Java 后端系统研发。"
    jd_hash = jd_extractor.jd_hash(jd_raw, "")
    conn.execute(
        "INSERT INTO positions (id, company_id, title, jd_raw, jd_hash, jd_struct_json)"
        " VALUES (1, 1, '高级 Java 研发工程师', ?, ?, ?)",
        (
            jd_raw,
            jd_hash if cached else "",
            json.dumps({"requirements": ["Java 后端"]}, ensure_ascii=False) if cached else "",
        ),
    )
    conn.commit()
    return conn


def test_compose_preview_get_does_not_generate_struct(monkeypatch):
    conn = _conn(cached=False)
    monkeypatch.setattr(prompts, "get_conn", lambda: conn)
    monkeypatch.setattr(prompts.jd_extractor, "ensure_struct", lambda *_args, **_kwargs: pytest.fail("GET must not generate JD struct"))

    with pytest.raises(HTTPException) as exc:
        prompts.compose_preview(1)

    assert exc.value.status_code == 409
    row = conn.execute("SELECT jd_struct_json FROM positions WHERE id=1").fetchone()
    assert row["jd_struct_json"] == ""


def test_compose_preview_post_generates_struct(monkeypatch):
    conn = _conn(cached=False)
    monkeypatch.setattr(prompts, "get_conn", lambda: conn)
    monkeypatch.setattr(prompts.job_label_dictionary, "compose_label_block", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(prompts.jd_extractor, "ensure_struct", lambda *_args, **_kwargs: {"requirements": ["Java 后端"]})

    payload = prompts.generate_compose_preview(1, {"id": 1})

    assert "system" in payload
    assert "Java 后端" in payload["user"]
