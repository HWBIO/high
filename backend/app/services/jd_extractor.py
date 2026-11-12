"""JD 结构化：JD 原文 → 结构化字段 + 原子化 requirements 列表（LLM，按 jd_hash 缓存）。

字段规范参考 playground/岗位JD生成-25/references/jd_output_schema.md。"""
import hashlib
import json

from . import llm

EXTRACT_SYSTEM = """你是招聘信息结构化助手。把岗位 JD 原文解析为结构化 JSON。

只输出合法 JSON 对象，不要任何其他文字：

{
  "position_name": "岗位名称",
  "years": "经验年限要求，如 '5年以上'，无则空字符串",
  "education": "学历要求，如 '本科及以上'，无则空字符串",
  "skills_required": ["必备技能1", "必备技能2"],
  "skills_preferred": ["加分技能1"],
  "city": "工作城市，无则空字符串",
  "salary": "薪资范围，无则空字符串",
  "hard_rules": ["硬性限制条件，如年龄、学校层次要求，无则空数组"],
  "requirements": ["原子化的岗位要求1", "原子化的岗位要求2"]
}

requirements 规则（最重要）：
- 把 JD 中的任职要求拆成一条条**原子要求**，每条只说一件事，保留原文关键表述
- 覆盖所有硬性与软性要求（技能、年限、学历、经验领域、软素质、特殊限制）
- 通常 4-12 条；不要把职责描述（"负责XX"）当成要求，除非它隐含能力要求"""


def jd_hash(jd_raw: str, consultant_notes: str = "") -> str:
    return hashlib.sha256((jd_raw + "\n#\n" + consultant_notes).encode("utf-8")).hexdigest()


def extract_jd_struct(title: str, jd_raw: str) -> dict:
    """一次 LLM 调用结构化一个 JD。返回 dict（含 requirements 列表）。"""
    user = f"岗位名称：{title}\n\nJD原文：\n{jd_raw}"
    try:
        parsed, _raw, tokens, _latency = llm.chat_json(EXTRACT_SYSTEM, user)
    except Exception as e:  # noqa: BLE001 需要把具体岗位和模型错误透给前端
        raise ValueError(f"岗位「{title}」JD 结构化失败（{type(e).__name__}: {e}）") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"JD 结构化输出不是对象: {type(parsed)}")
    parsed.setdefault("requirements", [])
    parsed["_tokens"] = tokens
    return parsed


def ensure_struct(conn, position_row) -> dict:
    """确保 position 有与当前 jd_raw 一致的结构化数据；变化时重新提取并落库。"""
    pos = dict(position_row)
    current_hash = jd_hash(pos["jd_raw"], pos.get("consultant_notes") or "")
    if pos.get("jd_hash") == current_hash and pos.get("jd_struct_json"):
        try:
            return json.loads(pos["jd_struct_json"])
        except json.JSONDecodeError:
            pass
    struct = extract_jd_struct(pos["title"], pos["jd_raw"])
    struct.pop("_tokens", None)
    conn.execute(
        "UPDATE positions SET jd_hash=?, jd_struct_json=?, updated_at=datetime('now','localtime') WHERE id=?",
        (current_hash, json.dumps(struct, ensure_ascii=False), pos["id"]),
    )
    conn.commit()
    return struct
