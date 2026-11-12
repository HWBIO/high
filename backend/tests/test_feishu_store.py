import sqlite3

from app.config import settings
from app.services import feishu_store


def test_public_url_requires_http_origin(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "")
    assert feishu_store._public_url("/results/1") == ""

    monkeypatch.setattr(settings, "public_base_url", "zhiwei.go2ready.com")
    assert feishu_store._public_url("/results/1") == ""

    monkeypatch.setattr(settings, "public_base_url", "http://zhiwei.go2ready.com")
    assert feishu_store._public_url("/results/1") == "http://zhiwei.go2ready.com/results/1"


def test_app_project_status_normalizes_legacy_values():
    assert feishu_store._app_project_status("待开始") == "未开始"
    assert feishu_store._app_project_status("进展中") == "进行中"
    assert feishu_store._app_project_status("Live") == "进行中"
    assert feishu_store._app_project_status("已完成") == "已结束"


def test_company_fields_keep_legal_name_and_short_name_separate():
    fields = feishu_store._company_fields({
        "id": 12,
        "name": "SUD",
        "legal_name": "上海数德科技有限公司",
        "short_name": "SUD",
        "extra_json": "{\"website\":\"sud.tech\",\"benefits\":\"五险一金\"}",
    })

    assert fields["本地ID"] == 12
    assert fields["营业执照名称"] == "上海数德科技有限公司"
    assert fields["公司名"] == "SUD"
    assert fields["公司简称"] == "SUD"
    assert fields["网址"] == "sud.tech"
    assert fields["福利"] == "五险一金"


def test_position_fields_include_restorable_job_fields():
    fields = feishu_store._position_fields({
        "id": 20,
        "title": "后端研发",
        "company": "上海数德科技有限公司",
        "salary_range": "50k-70k",
        "project_json": "{\"city\":\"上海\"}",
        "headcount": 2,
        "project_status": "进行中",
        "consultant": "顾问A",
        "hard_requirements": "必须 Java",
        "nice_to_have": "有 AI 经验",
        "jd_raw": "岗位职责...",
        "status": "active",
        "is_project": 1,
    })

    assert fields["本地ID"] == 20
    assert fields["岗位名称"] == "后端研发"
    assert fields["客户"] == "上海数德科技有限公司"
    assert fields["城市"] == "上海"
    assert fields["JD原文"] == "岗位职责..."
    assert fields["状态"] == "招聘中"
    assert fields["重点项目"] == "是"


def test_pull_legacy_company_name_becomes_short_name_without_erasing_legal_name(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            legal_name TEXT DEFAULT '',
            short_name TEXT DEFAULT '',
            city TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            status TEXT DEFAULT '开发中',
            notes TEXT DEFAULT '',
            extra_json TEXT DEFAULT '',
            feishu_record_id TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );
        """
    )
    conn.execute(
        "INSERT INTO companies (id, name, legal_name, short_name)"
        " VALUES (1, 'SUD', '上海数德科技有限公司', 'SUD')"
    )
    conn.commit()
    monkeypatch.setattr(feishu_store, "enabled", lambda: True)
    monkeypatch.setattr(
        feishu_store,
        "_rows",
        lambda _table_id: [{"record_id": "rec1", "fields": {"本地ID": 1, "公司名": "SUD"}}],
    )
    monkeypatch.setattr(settings, "feishu_table_companies", "tblCompanies")

    feishu_store.try_pull_companies(conn)

    row = conn.execute(
        "SELECT legal_name, short_name FROM companies WHERE id=1"
    ).fetchone()
    assert dict(row) == {"legal_name": "上海数德科技有限公司", "short_name": "SUD"}


def test_push_match_result_uses_feishu_url_object(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            resume_file TEXT
        );
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            title TEXT,
            company_id INTEGER
        );
        CREATE TABLE match_runs (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER
        );
        CREATE TABLE match_results (
            id INTEGER PRIMARY KEY,
            run_id INTEGER,
            position_id INTEGER,
            final_score REAL,
            exclude_reason TEXT,
            raw_llm_output TEXT,
            review_status TEXT,
            feishu_record_id TEXT
        );
        """
    )
    conn.execute("INSERT INTO candidates VALUES (1, '张三', '', '')")
    conn.execute("INSERT INTO companies VALUES (1, '客户A')")
    conn.execute("INSERT INTO positions VALUES (10, 'Java后端', 1)")
    conn.execute("INSERT INTO match_runs VALUES (100, 1)")
    conn.execute("INSERT INTO match_results VALUES (1000, 100, 10, 88.5, '匹配', '', 'pending', '')")
    conn.commit()
    captured = {}

    class FakeClient:
        def create_records(self, table_id, records):
            captured["table_id"] = table_id
            captured["fields"] = records[0]
            return {"records": [{"record_id": "rec1"}]}

    monkeypatch.setattr(feishu_store, "enabled", lambda: True)
    monkeypatch.setattr(feishu_store.feishu, "client", FakeClient())
    monkeypatch.setattr(settings, "public_base_url", "http://zhiwei.go2ready.com")
    monkeypatch.setattr(settings, "feishu_table_match_results", "tblMatch")

    feishu_store.push_match_result(conn, 1000)

    assert captured["table_id"] == "tblMatch"
    assert captured["fields"]["匹配报告"] == {
        "text": "查看匹配报告",
        "link": "http://zhiwei.go2ready.com/results/1000",
    }


def test_delete_position_ignores_feishu_record_not_found(monkeypatch):
    class FakeClient:
        def delete_record(self, table_id, record_id):
            raise RuntimeError("飞书接口失败：HTTP 200, code=1254043, msg=RecordIdNotFound")

    monkeypatch.setattr(feishu_store, "enabled", lambda: True)
    monkeypatch.setattr(feishu_store.feishu, "client", FakeClient())
    monkeypatch.setattr(settings, "feishu_table_positions", "tblPositions")
    monkeypatch.setattr(settings, "feishu_sync_strict", True)

    feishu_store.delete_position("recMissing")
