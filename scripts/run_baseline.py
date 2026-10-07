#!/usr/bin/env python3
"""基线自检：把每道题的**参考解**喂给判题器，看通过率是不是 100%。

这一步不是测学生代码，而是**测判题器本身有没有写错**。
如果参考解都过不了，说明问题在 harness（驱动脚本、断言切分、判题逻辑），
而不是在数据——先修 harness，再去跑别的实验。

用法：
    python scripts/run_baseline.py                          # 全部数据集
    python scripts/run_baseline.py humaneval                # 指定数据集
    python scripts/run_baseline.py humaneval --limit 20     # 只跑前 20 道，快速自检
    python scripts/run_baseline.py humaneval --jobs 4       # 并发（默认 1）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_judge.problems import load_problems_jsonl  # noqa: E402
from code_judge.references import load_references  # noqa: E402
from code_judge.sandbox import default_sandbox  # noqa: E402
from code_judge.types import Problem, Verdict  # noqa: E402
from code_judge.verifier import CodeVerifier  # noqa: E402


def judge_one(verifier: CodeVerifier, problem: Problem, source: str) -> dict:
    started = time.monotonic()
    report = verifier.verify(source, problem, "python")
    first = report.first_failure
    return {
        "id": problem.id,
        "verdict": report.verdict.value,
        "passed": report.passed_count,
        "total": report.total_count,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "failed_case": first.case_id if first else "",
        "detail": (first.detail if first else "")[:200],
        "stderr_tail": (first.stderr.strip().splitlines()[-1][:200] if first and first.stderr else ""),
    }


def run_dataset(name: str, limit: int | None, jobs: int) -> dict:
    problems_path = ROOT / "problems" / f"{name}.jsonl"
    solutions_path = ROOT / "solutions" / f"{name}.jsonl"
    if not problems_path.exists() or not solutions_path.exists():
        print(f"跳过 {name}：缺少 {problems_path.name} 或 {solutions_path.name}，"
              f"请先运行 scripts/import_datasets.py")
        return {}

    problems = load_problems_jsonl(problems_path)
    refs = load_references(solutions_path)
    if limit:
        problems = problems[:limit]

    sandbox = default_sandbox("local")  # 自检跑的是自己写的参考解，用本地沙箱足够
    verifier = CodeVerifier(sandbox=sandbox)

    started = time.monotonic()
    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            rows = list(
                pool.map(
                    lambda p: judge_one(verifier, p, refs[p.id].source),
                    problems,
                )
            )
    else:
        rows = [judge_one(verifier, p, refs[p.id].source) for p in problems]
    elapsed = time.monotonic() - started

    passed = sum(1 for r in rows if r["verdict"] == Verdict.AC.value)
    failures = [r for r in rows if r["verdict"] != Verdict.AC.value]
    total_cases = sum(r["total"] for r in rows)
    passed_cases = sum(r["passed"] for r in rows)

    print(f"\n=== {name} ===")
    print(f"题目 {len(rows)} 道，参考解通过 {passed} 道 "
          f"（{passed / max(1, len(rows)) * 100:.1f}%）")
    print(f"用例 {passed_cases}/{total_cases} 通过"
          f"（{passed_cases / max(1, total_cases) * 100:.1f}%）")
    print(f"耗时 {elapsed:.1f}s（{elapsed / max(1, len(rows)) * 1000:.0f} ms/题）")
    if failures:
        print(f"未通过 {len(failures)} 道，前 10 条：")
        for r in failures[:10]:
            print(f"  - {r['id']}  {r['verdict']}  {r['passed']}/{r['total']}"
                  f"  用例 {r['failed_case']}  {r['detail']}")
            if r["stderr_tail"]:
                print(f"      {r['stderr_tail']}")

    return {
        "dataset": name,
        "problems": len(rows),
        "passed_problems": passed,
        "pass_rate": passed / max(1, len(rows)),
        "passed_cases": passed_cases,
        "total_cases": total_cases,
        "elapsed_s": round(elapsed, 2),
        "sandbox": sandbox.name,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("datasets", nargs="*", default=None,
                        help="数据集名，默认全部已导入的")
    parser.add_argument("--limit", type=int, default=None, help="每个数据集只跑前 N 题")
    parser.add_argument("--jobs", type=int, default=1, help="并发数，默认 1")
    parser.add_argument("--out", default="eval/results/baseline.json")
    args = parser.parse_args()

    names = args.datasets or [
        p.stem for p in sorted((ROOT / "problems").glob("*.jsonl"))
    ]
    if not names:
        print("没有找到任何题库，请先运行 scripts/import_datasets.py")
        return 2

    summaries = [run_dataset(n, args.limit, args.jobs) for n in names]
    summaries = [s for s in summaries if s]

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {out_path.relative_to(ROOT)}")

    bad = [s for s in summaries if s["passed_problems"] != s["problems"]]
    if bad:
        print("\n！有参考解未通过——先查 harness（驱动脚本 / 断言切分 / 判题逻辑），"
              "再怀疑数据。")
        return 1
    print("\n全部参考解通过，判题链路自检成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
