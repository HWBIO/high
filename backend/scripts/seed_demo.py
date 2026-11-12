#!/usr/bin/env python3
"""种 8 个演示岗位（2 强匹配 AI 方向 / 2 边缘 / 4 明显不匹配）+ 一条公司提示词。

用法: ./.venv/bin/python scripts/seed_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn, init_db  # noqa: E402
from app.services.positions_helpers import ensure_company  # noqa: E402

POSITIONS = [
    # 强匹配（AI/大模型方向候选人）
    ("35", "AI agent 算法工程师（超线性）", "超线性", """任职要求：
1. 3年以上算法工程经验，本科及以上学历
2. 深入理解大模型与 Agent 架构：上下文压缩、记忆机制、Skills 实现、多智能体编排
3. 熟悉 RAG 检索增强生成，有向量库实战经验
4. 有模型微调（SFT/RLHF）经验者优先
5. 熟练使用 Python""", "客户重视工程落地能力，纯研究背景慎推"),
    ("44", "后端研发/AI agent 工程师（超线性）", "超线性", """任职要求：
1. 5年以上后端开发经验，熟练 Python/Golang 至少一门
2. 有 LLM 应用开发经验，熟悉 Agent 工具调用（Function Calling / MCP）
3. 熟悉高并发服务架构设计
4. 有 AI 基础设施或平台建设经验者优先""", ""),
    # 边缘
    ("13", "Agent 产品经理（AI Coding 方向）", "某大厂", """任职要求：
1. 5年以上产品经理经验，其中2年以上 AI 产品经验
2. 深入理解 AI Coding / 开发者工具产品
3. 有技术背景（能读代码）者优先
4. 本科及以上学历""", "需确认候选人产品转型意愿"),
    ("10", "AI 架构师（AI Agent & 游戏引擎方向）", "某游戏公司", """任职要求：
1. 主导 AI Agent 架构设计与落地：上下文压缩、记忆机制、Skills 实现、多智能体编排
2. 重构 MCP 工具调用生态
3. 主导游戏工程的 AI 友好度改造
4. 8年以上研发经验，5年以上架构经验
5. 熟练 C++/Python""", ""),
    # 明显不匹配
    ("1", "游戏引擎研发工程师", "某游戏公司", """任职要求：
1. 精通 C++，5年以上游戏引擎开发经验
2. 熟悉 Unreal/Unity 引擎源码
3. 熟悉渲染管线、物理引擎
4. 211/985 本科及以上""", ""),
    ("18", "广告商业化产品经理", "某大厂", """任职要求：
1. 5年以上广告/商业化产品经验
2. 熟悉程序化广告、DSP/SSP 生态
3. 有大规模广告平台从0到1经验""", ""),
    ("27", "大客户销售总监", "某SaaS公司", """任职要求：
1. 8年以上 To B 大客户销售经验
2. 有千万级年度销售业绩
3. 有团队管理经验（10人以上）""", ""),
    ("31", "高级视觉设计师", "某互联网公司", """任职要求：
1. 5年以上视觉设计经验
2. 精通 Figma/PS/AI，有完整的品牌设计案例
3. 艺术设计相关专业本科以上""", ""),
]

COMPANY_PROMPT = (
    "超线性",
    """超线性是一家 AI Agent 基础设施创业公司，团队技术密度高、节奏快。
隐性偏好（历史交付验证）：
1. 偏好有开源项目或技术博客的候选人
2. 大厂螺丝钉背景但无独立负责模块经验的候选人通过率低
3. 创业公司经历是加分项，稳定性要求相对宽松
4. 薪资预算弹性较大，但要求候选人对 AI 方向有真实热情（面试会深挖动机）""",
)


def main():
    init_db()
    conn = get_conn()
    existing = conn.execute("SELECT COUNT(*) AS c FROM positions").fetchone()["c"]
    if existing:
        print(f"positions 表已有 {existing} 条记录，跳过种子（如需重置请删除 data/hh.db）")
        return
    for seq_no, title, company, jd, notes in POSITIONS:
        company_id = ensure_company(conn, company)
        conn.execute(
            "INSERT INTO positions (company_id, source, seq_no, title, jd_raw, consultant_notes)"
            " VALUES (?,?,?,?,?,?)",
            (company_id, "manual", seq_no, title, jd, notes),
        )
    company_id = ensure_company(conn, COMPANY_PROMPT[0])
    conn.execute(
        "INSERT INTO prompts (layer, company_id, content, updated_by)"
        " VALUES ('company', ?, ?, 'seed')",
        (company_id, COMPANY_PROMPT[1]),
    )
    conn.commit()
    print(f"已种入 {len(POSITIONS)} 个演示岗位 + 1 条公司提示词（超线性）")


if __name__ == "__main__":
    main()
