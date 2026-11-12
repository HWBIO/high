#!/usr/bin/env python3
"""固定样本复跑稳定性检查。

用法：
  ./.venv/bin/python scripts/stability_regression.py <resume_file> <position_id> [rounds]

输出同一候选人 × 同一岗位多次匹配的综合参考得分、硬性条件得分、优先条件得分和波动范围。
用于观察提示词和模型输出是否稳定，不改变线上数据。
"""
import statistics
import sys
import time

import requests

BASE = "http://localhost:8720"


def wait_report(run_id: int) -> dict:
    for _ in range(180):
        time.sleep(2)
        status = requests.get(f"{BASE}/api/match/runs/{run_id}").json()
        if status["status"] == "failed":
            raise RuntimeError(status.get("error") or "run failed")
        if status["status"] in ("done", "done_with_errors") and status.get("progress", {}).get("valid_results", 0) > 0:
            page = requests.get(f"{BASE}/api/match/history-results?run_ids={run_id}&limit=1").json()
            items = page.get("items") or []
            if not items:
                raise RuntimeError(f"run {run_id} no result")
            return requests.get(f"{BASE}/api/match/results/{items[0]['result_id']}/report").json()
    raise TimeoutError(f"run {run_id} timeout")


def run_once(resume_file: str, position_id: int) -> dict:
    with open(resume_file, "rb") as fh:
        response = requests.post(
            f"{BASE}/api/match/batch-runs",
            data={"position_ids_json": f"[{position_id}]"},
            files=[("files", (resume_file, fh, "application/octet-stream"))],
        )
    response.raise_for_status()
    run_id = response.json()["run_ids"][0]
    report = wait_report(run_id)
    detail = report["details"][0]
    summary = detail.get("score_summary") or {}
    return {
        "run_id": run_id,
        "final_score": detail.get("final_score"),
        "hard_score": (summary.get("hard") or {}).get("score", 0),
        "priority_score": (summary.get("priority") or {}).get("score", 0),
        "judgement": summary.get("judgement", ""),
    }


def spread(values: list[float]) -> str:
    if not values:
        return "—"
    return f"min={min(values)}, max={max(values)}, range={round(max(values) - min(values), 1)}, stdev={round(statistics.pstdev(values), 2)}"


def main():
    if len(sys.argv) < 3:
        raise SystemExit("用法: stability_regression.py <resume_file> <position_id> [rounds]")
    resume_file = sys.argv[1]
    position_id = int(sys.argv[2])
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    rows = []
    for index in range(rounds):
        result = run_once(resume_file, position_id)
        rows.append(result)
        print(
            f"{index + 1}. run={result['run_id']} 综合参考={result['final_score']}分 "
            f"硬性={result['hard_score']}分 优先={result['priority_score']}分 | {result['judgement']}"
        )

    print("\n== 波动 ==")
    print("综合参考:", spread([row["final_score"] for row in rows]))
    print("硬性条件:", spread([row["hard_score"] for row in rows]))
    print("优先条件:", spread([row["priority_score"] for row in rows]))


if __name__ == "__main__":
    main()
