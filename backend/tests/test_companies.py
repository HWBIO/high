import sqlite3

import pytest
from fastapi import HTTPException

from app.routers import companies


@pytest.fixture
def company_conn(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            legal_name TEXT DEFAULT '',
            short_name TEXT DEFAULT '',
            city TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            contact_user_id INTEGER,
            contact_name TEXT DEFAULT '',
            status TEXT DEFAULT '开发中',
            notes TEXT DEFAULT '',
            extra_json TEXT DEFAULT '',
            feishu_record_id TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_companies_legal_name"
        " ON companies(legal_name) WHERE TRIM(legal_name)!=''"
    )
    monkeypatch.setattr(companies, "get_conn", lambda: conn)
    monkeypatch.setattr(companies.feishu_store, "push_company", lambda *_args, **_kwargs: None)
    return conn


def test_create_company_saves_legal_and_short_names(company_conn):
    result = companies.create_company(
        companies.CompanyIn(
            legal_name="  北京示例科技有限公司  ",
            short_name="示例科技",
        ),
        _user={"id": 1},
    )

    row = company_conn.execute(
        "SELECT name, legal_name, short_name FROM companies WHERE id=?",
        (result["id"],),
    ).fetchone()
    assert dict(row) == {
        "name": "北京示例科技有限公司",
        "legal_name": "北京示例科技有限公司",
        "short_name": "示例科技",
    }


def test_create_company_rejects_duplicate_legal_name(company_conn):
    body = companies.CompanyIn(legal_name="北京示例科技有限公司")
    companies.create_company(body, _user={"id": 1})

    with pytest.raises(HTTPException) as exc_info:
        companies.create_company(body, _user={"id": 1})

    assert exc_info.value.status_code == 409
    assert "营业执照名称" in exc_info.value.detail


def test_update_company_rejects_duplicate_legal_name(company_conn):
    first_id = companies.create_company(
        companies.CompanyIn(legal_name="北京示例科技有限公司"),
        _user={"id": 1},
    )["id"]
    companies.create_company(
        companies.CompanyIn(legal_name="上海示例信息技术有限公司"),
        _user={"id": 1},
    )

    with pytest.raises(HTTPException) as exc_info:
        companies.update_company(
            first_id,
            companies.CompanyUpdate(legal_name="上海示例信息技术有限公司"),
            _user={"id": 1},
        )

    assert exc_info.value.status_code == 409


def test_update_company_allows_blank_legal_name_for_legacy_company(company_conn):
    company_conn.execute(
        "INSERT INTO companies (name, legal_name, short_name) VALUES ('SUD', '', 'SUD')"
    )
    company_conn.commit()

    result = companies.update_company(
        1,
        companies.CompanyUpdate(legal_name="", short_name="SUD"),
        _user={"id": 1},
    )

    assert result == {"updated": True}
    row = company_conn.execute(
        "SELECT legal_name, short_name FROM companies WHERE id=1"
    ).fetchone()
    assert dict(row) == {"legal_name": "", "short_name": "SUD"}


def test_update_legacy_company_legal_name_updates_display_name(company_conn):
    company_conn.execute(
        "INSERT INTO companies (name, legal_name, short_name) VALUES ('yu', '', '')"
    )
    company_conn.commit()

    result = companies.update_company(
        1,
        companies.CompanyUpdate(legal_name="云起无垠", short_name=""),
        _user={"id": 1},
    )

    assert result == {"updated": True}
    row = company_conn.execute(
        "SELECT name, legal_name, short_name FROM companies WHERE id=1"
    ).fetchone()
    assert dict(row) == {"name": "云起无垠", "legal_name": "云起无垠", "short_name": ""}


def test_update_company_extra_merges_partial_payload(company_conn):
    company_conn.execute(
        "INSERT INTO companies (name, legal_name, short_name, extra_json)"
        " VALUES ('云起无垠', '云起无垠', '', '{\"website\":\"old.cn\",\"financing\":\"A轮\"}')"
    )
    company_conn.commit()

    result = companies.update_company(
        1,
        companies.CompanyUpdate(extra={"website": "new.cn"}),
        _user={"id": 1},
    )

    assert result == {"updated": True}
    row = company_conn.execute("SELECT extra_json FROM companies WHERE id=1").fetchone()
    assert row["extra_json"] == '{"website": "new.cn", "financing": "A轮"}'
