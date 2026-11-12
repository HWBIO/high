#!/usr/bin/env python3
"""端到端冒烟测试：需要共享后端已启动，且 .env 配好 LLM / 飞书。

用法:
  SMOKE_USERNAME=xxx SMOKE_PASSWORD=xxx ./.venv/bin/python scripts/smoke_e2e.py <候选人简历.pdf 的相对/绝对路径>

流程: health → 登录 → 确认项目库 → 上传临时简历并发起匹配 →
轮询到可用终态 → 校验报告形态 → 加岗位专属提示词重跑，验证三层提示词生效。
"""
import os
import sys
import time
from pathlib import Path

import requests

BASE = os.environ.get("SMOKE_BASE_URL", os.environ.get("APP_PUBLIC_URL", "http://127.0.0.1:8720")).rstrip("/")
SESSION = requests.Session()


def die(msg: str):
    print(f"✗ {msg}")
    sys.exit(1)


def wait_run(run_id: int, timeout: int = 600) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        status = SESSION.get(f"{BASE}/api/match/runs/{run_id}", timeout=10).json()
        print(f"  run {run_id}: {status['status']} "
              f"(tokens={status['total_tokens']})", flush=True)
        if status["status"] in ("done", "done_with_errors") and status.get("progress", {}).get("valid_results", 0) > 0:
            return status
        if status["status"] == "failed":
            die(f"run 失败: {status['error']}")
        time.sleep(3)
    die("run 超时")


def first_result_report(run_id: int) -> dict:
    page = SESSION.get(
        f"{BASE}/api/match/history-results?run_ids={run_id}&limit=1",
        timeout=10,
    ).json()
    items = page.get("items") or []
    if not items:
        die(f"run {run_id} 没有生成匹配结果")
    return SESSION.get(
        f"{BASE}/api/match/results/{items[0]['result_id']}/report",
        timeout=10,
    ).json()


def login() -> None:
    username = os.environ.get("SMOKE_USERNAME", "").strip()
    password = os.environ.get("SMOKE_PASSWORD", "")
    if not username or not password:
        die("请设置 SMOKE_USERNAME 和 SMOKE_PASSWORD，用已有 Agent 账号执行冒烟测试")
    resp = SESSION.post(
        f"{BASE}/api/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    if resp.status_code != 200:
        die(f"登录失败: {resp.text}")
    token = resp.json().get("token")
    if not token:
        die("登录成功但未返回 token")
    SESSION.headers.update({"Authorization": f"Bearer {token}"})


def main():
    if len(sys.argv) < 2:
        die("用法: smoke_e2e.py <简历.pdf>")
    resume_path = Path(sys.argv[1])
    if not resume_path.exists():
        die(f"简历文件不存在: {resume_path}")

    # 1. health
    try:
        assert SESSION.get(f"{BASE}/health", timeout=5).json()["ok"]
    except Exception as e:  # noqa: BLE001
        die(f"后端不可用（{BASE}）: {e}")
    print("✓ 后端存活")
    login()
    print("✓ 账号登录成功")

    # 2. 项目库
    positions = [p for p in SESSION.get(f"{BASE}/api/positions", timeout=10).json() if p.get("is_project")]
    if not positions:
        die("项目库为空，请先在「项目管理」选择重点项目")
    print(f"✓ 项目库 {len(positions)} 个项目")

    # 3. 上传临时简历并发起匹配（取前 3 个项目，避免冒烟测试过重）
    selected_ids = [p["id"] for p in positions[:3]]
    with open(resume_path, "rb") as f:
        resp = SESSION.post(
            f"{BASE}/api/match/batch-runs",
            data={"position_ids_json": str(selected_ids)},
            files=[("files", (resume_path.name, f, "application/pdf"))],
            timeout=60,
        )
    if resp.status_code != 200:
        die(f"批量匹配创建失败: {resp.text}")
    data = resp.json()
    run_id = data["run_ids"][0]
    print(f"✓ run {run_id} 已发起，等待完成…")
    wait_run(run_id)

    # 4. 校验报告形态
    report = first_result_report(run_id)
    details, md = report["details"], report["markdown"]
    for d in details:
        assert d["final_score"] is not None
        for row in d["rows"]:
            assert row["label"] in ("满足", "待确认", "不满足"), row
            assert row["condition_type"] in ("hard", "priority"), row
        if d["final_score"] >= 90:
            assert d["recommend_level"] == "top"
        elif d["final_score"] >= 80:
            assert d["recommend_level"] in ("review", "caution")
        assert len(d["suggestions"]) >= 1, "建议不能为空"
    assert "## 一、岗位逐项匹配明细" in md
    print(f"✓ 报告形态正确：明细 {len(details)} 岗")

    # 6. 三层提示词有效性：给明细分数最高的岗位加一条硬性排除规则，重跑
    if not details:
        print("⚠ 无可用明细，跳过三层提示词验证")
        return
    target = details[0]
    SESSION.post(
        f"{BASE}/api/prompts",
        json={
            "layer": "position",
            "position_id": target["position_id"],
            "content": "硬性规则：候选人必须有10年以上量子计算研发经验，否则判不满足，并在报告中提示请重点关注、慎重推荐。",
            "updated_by": "smoke_e2e",
        },
        timeout=10,
    ).raise_for_status()
    with open(resume_path, "rb") as f:
        resp = SESSION.post(
            f"{BASE}/api/match/batch-runs",
            data={"position_ids_json": f"[{target['position_id']}]"},
            files=[("files", (resume_path.name, f, "application/pdf"))],
            timeout=60,
        )
    run2 = resp.json()["run_ids"][0]
    print(f"✓ 已添加岗位专属硬性规则，重跑 run {run2}…")
    wait_run(run2)
    report2 = first_result_report(run2)
    new_score = None
    for d in report2["details"]:
        if d["position_id"] == target["position_id"]:
            new_score = d["final_score"]
    if new_score is None:
        print(f"✓ 三层提示词生效：岗位未生成明细（原综合参考分 {target['final_score']}分）")
    else:
        assert new_score < target["final_score"], (
            f"加硬性排除规则后分数未下降: {target['final_score']} → {new_score}"
        )
        print(f"✓ 三层提示词生效：综合参考分 {target['final_score']}分 → {new_score}分")

    print("\n全部通过 ✓  完整报告 markdown 预览：\n")
    print(report["markdown"][:2000])


if __name__ == "__main__":
    main()
