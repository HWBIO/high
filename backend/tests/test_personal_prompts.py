import sqlite3

import pytest
from fastapi import HTTPException

from app.db import SCHEMA
from app.routers import prompts
from app.services import prompt_composer


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role)"
        " VALUES (1, 'admin', 'x', '管理员', 'business_admin')"
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role)"
        " VALUES (2, 'consultant', 'x', '顾问A', 'consultant')"
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role)"
        " VALUES (3, 'other', 'x', '顾问B', 'consultant')"
    )
    conn.execute(
        "INSERT INTO prompts (id, layer, content, is_active, version, scope, updated_by)"
        " VALUES (10, 'general', '公司通用', 1, 1, 'company_default', 'admin')"
    )
    conn.execute(
        "INSERT INTO prompts (id, layer, content, is_active, version, scope, owner_user_id, owner_name, updated_by)"
        " VALUES (20, 'general', '顾问A个人', 1, 1, 'personal', 2, '顾问A', '顾问A')"
    )
    conn.execute(
        "INSERT INTO user_prompt_preferences (user_id, general_prompt_id, general_prompt_source)"
        " VALUES (2, 20, 'personal')"
    )
    conn.commit()
    return conn


def test_list_prompts_returns_company_and_own_personal_only(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(prompts, "get_conn", lambda: conn)

    own = prompts.list_prompts(layer="general", include_history=True, user={"id": 2})
    other = prompts.list_prompts(layer="general", include_history=True, user={"id": 3})

    assert {item["id"] for item in own} == {10, 20}
    assert {item["id"] for item in other} == {10}
    assert next(item for item in own if item["id"] == 20)["is_effective"] == 1


def test_consultant_can_save_personal_but_not_company_general(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(prompts, "get_conn", lambda: conn)

    saved = prompts.save_prompt(
        prompts.PromptIn(layer="general", scope="personal", content="新的个人提示词"),
        {"id": 2, "display_name": "顾问A", "role": "consultant"},
    )
    assert saved["version"] == 2
    assert prompt_composer.resolve_active_prompt(conn, "general", user_id=2) == "新的个人提示词"

    with pytest.raises(HTTPException) as exc:
        prompts.save_prompt(
            prompts.PromptIn(layer="general", scope="company_default", content="新的公司通用"),
            {"id": 2, "display_name": "顾问A", "role": "consultant"},
        )
    assert exc.value.status_code == 403


def test_apply_company_prompt_is_user_preference(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(prompts, "get_conn", lambda: conn)

    prompts.activate_prompt(10, {"id": 2, "display_name": "顾问A", "role": "consultant"})

    assert prompt_composer.resolve_active_prompt(conn, "general", user_id=2) == "公司通用"
    assert prompt_composer.resolve_active_prompt(conn, "general", user_id=3) == "公司通用"
