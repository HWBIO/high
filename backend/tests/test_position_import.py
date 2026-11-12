import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SCHEMA, _migrate
from app.routers import positions
from app.routers.positions import (
    PositionIn,
    PositionUpdate,
    _existing_position_for_import,
    _extract_positions_from_handbook_text,
    _extract_positions_from_xlsx,
    _clean_struct,
    _compose_jd_from_project,
    _normalize_project_status,
    parse_position_text,
    PositionParseIn,
    create_position,
    update_position,
    _position_xlsx_temp_path,
    _safe_upload_display_name,
    _validate_project_dates,
    upload_positions,
)


DELTIC_HANDBOOK_TEXT = """
DELTIC AI
智能跃迁
人才招聘手册
关于我们 · AI Native Company

01｜招聘概览

1
财务主管 / Finance Lead / Finance Supervisor
不是记账型财务，而是能陪公司从 0 到 1 建立财务秩序的人
HC
1
地点
北京
经验参考
5-8 年
核心关键词
全盘财务 / 财务体系搭建 / 经营分析 / 合规 / 融资支持
岗位职责
负责公司日常财务核算、报表、预算、税务及资金管理；
搭建适合早期 AI 公司的财务流程与内控机制；
支持融资、审计、政府项目申报等工作。
我们希望你具备
财务基本功扎实，能独立完成全盘财务工作；
熟悉科技公司或创业公司财务管理场景；
能把财务语言转化为经营决策语言。
加分项
有投融资、政府补贴申报或高新技术企业相关经验；
在创业公司从 0 到 1 搭建过财务体系。
可以放宽
不要求大厂背景；
不要求英文极强，但需要能阅读基础合同与财务资料。

2
研发经理 / 研发组长 / Engineering Manager / Tech Lead
懂技术，也懂如何带人、拆目标和交付
HC
1-2
地点
北京优先，可接受远程协作
经验参考
8 年以上
核心关键词
AI 工程化 / 团队管理 / 后端架构 / 交付 / LLM 应用
岗位职责
负责 AI 产品研发团队的技术规划、任务拆解和交付管理；
参与核心系统架构设计与关键技术决策。
我们希望你具备
有扎实工程能力，能判断技术方案是否可长期维护；
有 3 人以上研发团队管理经验；
理解 LLM 应用开发、数据流和模型调用链路。
加分项
有 AI Agent、RAG 或企业级 SaaS 产品经验。

3
AI 核心技术人才 / AI Core Talent
长期开放岗位，寻找能定义下一代 AI 产品技术边界的人
HC
长期开放，不设固定 HC
地点
北京 / 远程均可
重点方向
LLM 应用架构
AI Agent
多模态理解与生成
模型评测与数据闭环
我们真正看重什么
你能否提出高质量技术问题；
你是否有从研究到产品落地的完整判断力；
你是否能把不确定问题拆成可验证路径。
我们接受的非标准候选人
没有大厂履历，但有高质量开源项目；
学历路径不典型，但技术作品足够扎实；
研究背景强，愿意走向产品工程落地。

06｜我们如何看一个候选人
我们不机械套用简历标签。
"""


def test_extract_positions_from_word_handbook_text():
    items = _extract_positions_from_handbook_text(DELTIC_HANDBOOK_TEXT, "Deltic_AI_智能跃迁_云起无垠 人才招聘手册_.docx")

    assert len(items) == 3
    assert items[0]["company"] == "DELTIC AI / 智能跃迁"
    assert items[0]["seq_no"] == "1"
    assert items[0]["title"] == "财务主管 / Finance Lead / Finance Supervisor"
    assert items[0]["headcount"] == 1
    assert items[0]["project"]["city"] == "北京"
    assert "财务基本功扎实" in items[0]["hard_requirements"]
    assert "投融资" in items[0]["nice_to_have"]
    assert "核心关键词：全盘财务" in items[0]["jd_raw"]
    assert items[0]["hard_struct"] == {}

    assert items[1]["headcount"] == 1
    assert "AI 产品研发团队" in items[1]["jd_raw"]

    assert items[2]["title"] == "AI 核心技术人才 / AI Core Talent"
    assert items[2]["headcount"] == 1
    assert items[2]["project"]["city"] == "北京"
    assert "重点方向" in items[2]["consultant_notes"]
    assert "高质量开源项目" in items[2]["nice_to_have"]
    assert "06｜我们如何看一个候选人" not in items[2]["jd_raw"]


def test_upload_positions_uses_handbook_parser_before_llm(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    async def fake_extract_upload_text(_file, min_chars=50):
        assert min_chars == 50
        return DELTIC_HANDBOOK_TEXT, "Deltic_AI_智能跃迁_云起无垠 人才招聘手册_.docx"

    class FakeUpload:
        filename = "Deltic_AI_智能跃迁_云起无垠 人才招聘手册_.docx"

    monkeypatch.setattr(positions.document_importer, "extract_upload_text", fake_extract_upload_text)
    monkeypatch.setattr(
        positions.llm,
        "chat_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )
    monkeypatch.setattr(positions, "get_conn", lambda: conn)
    monkeypatch.setattr(positions.feishu_store, "push_company", lambda *args, **kwargs: None)
    monkeypatch.setattr(positions.feishu_store, "push_position", lambda *args, **kwargs: None)

    result = asyncio.run(upload_positions(FakeUpload(), _user={"id": 1}))

    assert result["count"] == 3
    rows = conn.execute(
        "SELECT p.seq_no, p.title, p.jd_raw, p.project_json, c.name AS company"
        " FROM positions p LEFT JOIN companies c ON c.id=p.company_id ORDER BY p.id"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0]["company"] == "DELTIC AI / 智能跃迁"
    assert rows[0]["seq_no"] == "1"
    assert rows[0]["title"] == "财务主管 / Finance Lead / Finance Supervisor"
    assert "岗位职责" in rows[0]["jd_raw"]
    assert json.loads(rows[0]["project_json"])["city"] == "北京"


def test_extract_positions_from_grouped_header_xlsx(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "JD"
    sheet.append(["招聘需求", "", "", "", "", "", "", "顾问备注"])
    sheet.append(["序号", "工作地点", "招聘岗位名称", "薪资范围", "岗位职责", "任职要求", "备注说明"])
    sheet.append([
        "1",
        "上海",
        "高级引擎研发工程师（图形）",
        "40-60k",
        "开发、维护、重构图形后端。",
        "3年以上C++编程经验；熟悉一种以上图形API。",
        "重点关注图形渲染经验。",
    ])
    path = tmp_path / "positions.xlsx"
    workbook.save(path)

    items = _extract_positions_from_xlsx(path)

    assert len(items) == 1
    assert items[0]["seq_no"] == "1"
    assert items[0]["title"] == "高级引擎研发工程师（图形）"
    assert items[0]["company"] == ""
    assert items[0]["salary_range"] == "40-60k"
    assert items[0]["project"]["city"] == "上海"
    assert "开发、维护、重构图形后端" in items[0]["jd_raw"]
    assert "3年以上C++编程经验" in items[0]["hard_requirements"]
    assert "重点关注图形渲染经验" in items[0]["consultant_notes"]


def test_extract_positions_uses_filename_as_company_fallback(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["招聘需求", "", "", "", "", "", "", "顾问备注"])
    sheet.append(["序号", "工作地点", "招聘岗位名称", "薪资范围", "岗位职责", "任职要求", "备注说明"])
    sheet.append([
        "1",
        "厦门/深圳/北京/上海",
        "高级引擎研发工程师（架构）",
        "70-100k",
        "引擎基础框架的开发、维护工作。",
        "3年以上C++开发经验。",
        "",
    ])
    path = tmp_path / "知微全部岗位jd.xlsx"
    workbook.save(path)

    items = _extract_positions_from_xlsx(path)

    assert len(items) == 1
    assert items[0]["company"] == "知微"


def test_extract_positions_reads_late_consultant_note(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "JD"
    sheet.append(["序号", "工作地点", "招聘岗位名称", "薪资范围", "岗位职责", "任职要求", "顾问后期备注"])
    sheet.append([
        "2",
        "上海",
        "解决方案架构师（SA）",
        "50-80k",
        "负责客户解决方案架构设计。",
        "熟悉云服务和售前技术支持。",
        "客户更看重海外项目交付经验。",
    ])
    path = tmp_path / "positions.xlsx"
    workbook.save(path)

    items = _extract_positions_from_xlsx(path)

    assert len(items) == 1
    assert items[0]["consultant_notes"] == "客户更看重海外项目交付经验。"


def test_extract_positions_reads_monthly_salary_and_structured_candidate_fields(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "JD"
    sheet.append(["序号", "工作地点", "招聘岗位名称", "月薪", "语言能力", "名企背景", "性别", "岗位职责", "任职要求"])
    sheet.append([
        "1",
        "北京",
        "后端研发工程师",
        "50k-70k",
        "英语可作为工作语言",
        "是",
        "不限",
        "负责服务端架构。",
        "熟悉 Java 和分布式系统。",
    ])
    path = tmp_path / "positions.xlsx"
    workbook.save(path)

    items = _extract_positions_from_xlsx(path)

    assert len(items) == 1
    assert items[0]["salary_range"] == "50k-70k"
    assert items[0]["hard_struct"]["language"] == "英语可作为工作语言"
    assert items[0]["hard_struct"]["company_background"] == "是"
    assert items[0]["hard_struct"]["gender"] == "不限"
    assert "cities" not in items[0]["hard_struct"]
    assert "salary_range" not in items[0]["hard_struct"]


def test_clean_struct_keeps_structured_candidate_fields():
    data = _clean_struct({
        "language": "英语可作为工作语言",
        "company_background": "是",
        "gender": "女",
        "nationality": "中国",
    })

    assert data == {
        "language": "英语可作为工作语言",
        "company_background": "是",
        "gender": "女",
    }


def test_compose_jd_from_project_keeps_structured_project_fields():
    text = _compose_jd_from_project(PositionIn(
        title="解决方案架构师",
        project={
            "contact": "张三",
            "fee": "20%",
            "priority": "高",
            "industry": "游戏",
            "function": "售前解决方案",
            "city": "上海",
            "address": "浦东",
            "guarantee_months": "3个月",
            "start_date": "2026-09-02",
            "end_date": "2026-10-02",
        },
        salary_range="50k-70k",
        consultant_notes="客户更看重海外交付经验",
        hard_struct={
            "language": "英语可作为工作语言",
            "must_skills": ["云服务", "客户方案设计"],
        },
    ))

    assert "岗位名称：解决方案架构师" in text
    assert "项目优先级：高" in text
    assert "行业：游戏" in text
    assert "职能：售前解决方案" in text
    assert "开始日期：2026-09-02" in text
    assert "顾问备注" in text
    assert "必须技能：云服务、客户方案设计" in text


def test_create_position_accepts_company_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO companies (id, name, legal_name) VALUES (7, 'Cocos/SUD', 'Cocos/SUD')")
    conn.commit()

    monkeypatch.setattr(positions, "get_conn", lambda: conn)
    monkeypatch.setattr(positions.feishu_store, "push_company", lambda *args, **kwargs: None)
    monkeypatch.setattr(positions.feishu_store, "push_position", lambda *args, **kwargs: None)

    result = create_position(PositionIn(
        company_id=7,
        company="不会新建这个客户",
        title="新增岗位",
        project={
            "priority": "高",
            "industry": "游戏",
            "function": "后端研发",
            "city": "北京",
            "start_date": "2026-09-02",
        },
    ), _user={"id": 1})

    assert result["id"]
    assert result["company_id"] == 7
    position = conn.execute("SELECT company_id, jd_raw FROM positions WHERE id=?", (result["id"],)).fetchone()
    company_count = conn.execute("SELECT COUNT(*) AS c FROM companies").fetchone()["c"]
    assert position["company_id"] == 7
    assert "职能：后端研发" in position["jd_raw"]
    assert company_count == 1


def test_create_position_dedupes_immediate_duplicate_submit(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO companies (id, name, legal_name) VALUES (7, 'Cocos/SUD', 'Cocos/SUD')")
    conn.commit()

    monkeypatch.setattr(positions, "get_conn", lambda: conn)
    monkeypatch.setattr(positions.feishu_store, "push_company", lambda *args, **kwargs: None)
    monkeypatch.setattr(positions.feishu_store, "push_position", lambda *args, **kwargs: None)

    body = PositionIn(
        company_id=7,
        title="解决方案架构师（SA）",
        jd_raw="负责售前方案设计和客户技术沟通。",
        project={"priority": "高", "industry": "游戏", "function": "售前解决方案", "city": "北京", "start_date": "2026-09-02"},
        hard_struct={"must_skills": ["云服务", "客户方案设计"]},
    )
    first = create_position(body, _user={"id": 1})
    second = create_position(body, _user={"id": 1})

    assert second["id"] == first["id"]
    assert second["deduped"] is True
    assert conn.execute("SELECT COUNT(*) AS c FROM positions").fetchone()["c"] == 1


def test_create_position_rejects_unknown_company_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    monkeypatch.setattr(positions, "get_conn", lambda: conn)

    try:
        create_position(PositionIn(
            company_id=404,
            title="新增岗位",
            project={
                "priority": "高",
                "industry": "游戏",
                "function": "后端研发",
                "city": "北京",
                "start_date": "2026-09-02",
            },
        ), _user={"id": 1})
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "status_code", None) == 400
        assert "客户不存在" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("unknown company_id should be rejected")


def test_update_position_accepts_company_id_without_creating_company(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO companies (id, name, legal_name) VALUES (7, '旧客户', '旧客户')")
    conn.execute("INSERT INTO companies (id, name, legal_name) VALUES (8, '新客户', '新客户')")
    conn.execute(
        "INSERT INTO positions (id, company_id, source, title, jd_raw)"
        " VALUES (100, 7, 'manual', '后端研发', 'jd')"
    )
    conn.commit()

    pushed = []
    monkeypatch.setattr(positions, "get_conn", lambda: conn)
    monkeypatch.setattr(positions.feishu_store, "push_company", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(positions.feishu_store, "push_position", lambda _conn, position_id, **_kwargs: pushed.append(position_id))

    result = update_position(100, PositionUpdate(company_id=8), _user={"id": 1})

    assert result == {"updated": True}
    row = conn.execute("SELECT company_id FROM positions WHERE id=100").fetchone()
    company_count = conn.execute("SELECT COUNT(*) AS c FROM companies").fetchone()["c"]
    assert row["company_id"] == 8
    assert company_count == 2
    assert pushed == [100]


def test_update_position_rejects_unknown_company_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO positions (id, source, title, jd_raw) VALUES (100, 'manual', '后端研发', 'jd')")
    conn.commit()

    monkeypatch.setattr(positions, "get_conn", lambda: conn)

    try:
        update_position(100, PositionUpdate(company_id=404), _user={"id": 1})
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "status_code", None) == 400
        assert "客户不存在" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("unknown company_id should be rejected")


def test_parse_position_text_returns_form_fields_without_duplicate_hard_conditions(monkeypatch):
    def fake_chat_json(_system, _user):
        return {
            "positions": [{
                "company": "Cocos/SUD",
                "title": "后端研发工程师",
                "headcount": 2,
                "salary_range": "50k-70k",
                "project_status": "进行中",
                "unstructured_summary": "原文还有复杂业务背景要求，需要顾问确认是否加入岗位提示词。",
                "jd_raw": "负责服务端架构，要求 Java 和分布式经验。",
                "project": {
                    "contact": "张三",
                    "fee": "20%",
                    "priority": "高",
                    "industry": "游戏",
                    "function": "后端研发",
                    "city": "北京",
                    "guarantee_months": "3个月",
                    "start_date": "2026-08-26",
                    "end_date": "2026-09-26",
                },
                "hard_struct": {
                    "language": "英语可作为工作语言",
                    "company_background": "是",
                    "gender": "不限",
                    "arrival_time": "1个月内",
                    "must_skills": ["Java", "分布式"],
                    "cities": ["北京"],
                    "salary_range": "50k-70k",
                },
            }]
        }, "", 123, 0

    monkeypatch.setattr(positions.llm, "chat_json", fake_chat_json)

    result = parse_position_text(PositionParseIn(text="这是一个足够长的岗位JD文本，用于触发解析。"), _user={"id": 1})

    assert result["position"]["company"] == "Cocos/SUD"
    assert result["position"]["project"]["city"] == "北京"
    assert result["position"]["salary_range"] == "50k-70k"
    assert result["unstructured_summary"] == "原文还有复杂业务背景要求，需要顾问确认是否加入岗位提示词。"
    assert result["position"]["hard_struct"]["must_skills"] == ["Java", "分布式"]
    assert "cities" not in result["position"]["hard_struct"]
    assert "salary_range" not in result["position"]["hard_struct"]


def test_validate_project_dates_requires_end_after_start():
    _validate_project_dates({"start_date": "2026-08-26", "end_date": "2026-08-27"})
    try:
        _validate_project_dates({"start_date": "2026-08-26", "end_date": "2026-08-26"})
    except Exception as exc:  # noqa: BLE001
        assert "结束日期必须晚于开始日期" in str(exc)
    else:
        raise AssertionError("expected invalid equal dates")


def test_existing_position_for_import_is_company_scoped():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            title TEXT,
            company_id INTEGER,
            status TEXT
        );
        """
    )
    conn.execute("INSERT INTO positions VALUES (1, '销售经理', 10, 'active')")
    conn.execute("INSERT INTO positions VALUES (2, '销售经理', NULL, 'active')")
    conn.commit()

    assert _existing_position_for_import(conn, "销售经理", 10)["id"] == 1
    assert _existing_position_for_import(conn, "销售经理", 20)["id"] == 2
    assert _existing_position_for_import(conn, "销售经理", None)["id"] == 2


def test_project_status_aliases_normalize_to_standard_values():
    assert _normalize_project_status("待开始") == "未开始"
    assert _normalize_project_status("进展中") == "进行中"
    assert _normalize_project_status("Live") == "进行中"
    assert _normalize_project_status("已完成") == "已结束"


def test_migrate_normalizes_dirty_project_status_values():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO positions (id, title, jd_raw, project_status) VALUES (1, 'A', 'JD', 'Live')")
    conn.execute("INSERT INTO positions (id, title, jd_raw, project_status) VALUES (2, 'B', 'JD', '待开始')")
    conn.execute("INSERT INTO positions (id, title, jd_raw, project_status) VALUES (3, 'C', 'JD', '进展中')")

    _migrate(conn)

    rows = conn.execute("SELECT id, project_status FROM positions ORDER BY id").fetchall()
    assert [row["project_status"] for row in rows] == ["进行中", "未开始", "进行中"]


def test_position_xlsx_temp_path_is_unique_and_not_based_on_upload_name(tmp_path, monkeypatch):
    monkeypatch.setattr(positions.time, "time", lambda: 1787650000)

    values = iter(["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"])

    class StubUuid:
        @property
        def hex(self):
            return next(values)

    monkeypatch.setattr(positions.uuid, "uuid4", lambda: StubUuid())

    first = _position_xlsx_temp_path(tmp_path)
    second = _position_xlsx_temp_path(tmp_path)

    assert first.name == "import_xlsx_1787650000_aaaaaaaa.xlsx"
    assert second.name == "import_xlsx_1787650000_bbbbbbbb.xlsx"
    assert first != second
    assert first.parent == tmp_path


def test_safe_upload_display_name_strips_client_path():
    assert _safe_upload_display_name("../../岗位信息.xlsx") == "岗位信息.xlsx"
    assert _safe_upload_display_name(r"C:\fakepath\岗位信息.xlsx") == "岗位信息.xlsx"


def test_position_upload_rejects_legacy_excel_extensions():
    class FakeUpload:
        filename = "岗位.xls"

        async def read(self):
            return b"not used"

    try:
        asyncio.run(upload_positions(FakeUpload(), _user={"id": 1}))
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "status_code", None) == 422
        assert "仅支持 .xlsx" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("legacy Excel extension should be rejected")
