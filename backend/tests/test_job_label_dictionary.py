import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import job_label_dictionary


def _row(family, capability, synonyms="", description=""):
    return job_label_dictionary.JobLabelRow(
        family=family,
        capability=capability,
        description=description or f"{capability}能力",
        synonyms=synonyms,
        positive_evidence=f"{capability}正向证据",
        negative_boundary=f"{capability}反向边界",
    )


def test_relevant_label_rows_filters_family_then_capability():
    rows = [
        _row("技术研发", "Java后端研发", "Java, Spring, JVM"),
        _row("技术研发", "服务端基础架构", "分布式, 高并发, SLA"),
        _row("产品", "AI产品", "AI产品, RAG"),
        _row("财务", "总账/核算", "月结, 报表"),
    ]
    position = {
        "title": "高级Java研发工程师",
        "jd_raw": "负责Java服务端、Spring微服务、高并发链路和JVM调优。",
        "jd_struct": {},
    }

    selected, families = job_label_dictionary.relevant_label_rows(position, rows=rows)

    assert families[0] == "技术研发"
    assert [row.capability for row in selected][:2] == ["Java后端研发", "服务端基础架构"]
    assert all(row.family == "技术研发" for row in selected)


def test_compose_label_block_contains_required_fields(monkeypatch):
    rows = [
        _row("运营", "广告变现运营", "广告变现, eCPM, 填充率"),
        _row("销售/商务", "广告销售", "广告销售, 客户, 回款"),
    ]
    monkeypatch.setattr(job_label_dictionary, "label_rows", lambda: rows)
    position = {"title": "广告变现运营", "jd_raw": "提升eCPM和填充率", "jd_struct": {}}

    block = job_label_dictionary.compose_label_block(position)

    assert "## 岗位能力标签词典片段" in block
    assert "岗位族Label：运营" in block
    assert "下设能力：广告变现运营" in block
    assert "关键词/同义词" in block
    assert "反向边界" in block
