#!/usr/bin/env python3
"""生成带错因标签的变异数据集。

流程：参考解 → 注入已知错误 → 判题机验证「确实失败了」→ 保留成带标签样本。

用法：
    python scripts/mutate.py --limit 40 --jobs 4          # 先小规模验证
    python scripts/mutate.py --jobs 4                     # 全量
    python scripts/mutate.py --operators boundary_condition,off_by_one_plus

产出：
    eval/datasets/mutants.jsonl    有效样本（只含确实失败的变异体）
    eval/results/mutation.json     统计报告（含等价变异率）
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_judge.mutation import generate_mutants  # noqa: E402
from code_judge.mutation.dataset import LabelledMutant, save_mutants  # noqa: E402
from code_judge.problems import load_problems_jsonl  # noqa: E402
from code_judge.references import load_references  # noqa: E402
from code_judge.sandbox import default_sandbox  # noqa: E402
from code_judge.types import Verdict  # noqa: E402
from code_judge.verifier import CodeVerifier  # noqa: E402

#: 变异体只需要判断「是否失败」，不需要精确的时间语义。用更短的上限能显著加速，
#: 否则一个死循环变异体就要吃掉完整的题目时限。
DEFAULT_MUTANT_TIME_LIMIT_MS = 1500


def build_tasks(names: list[str], limit: int | None, max_per_operator: int, operators):
    """展开成 (题目, 变异体) 的任务列表。"""
    tasks = []
    for name in names:
        problems_path = ROOT / "problems" / f"{name}.jsonl"
        solutions_path = ROOT / "solutions" / f"{name}.jsonl"
        if not problems_path.exists() or not solutions_path.exists():
            print(f"跳过 {name}：请先运行 scripts/import_datasets.py")
            continue
        problems = load_problems_jsonl(problems_path)
        if limit:
            problems = problems[:limit]
        refs = load_references(solutions_path)
        for problem in problems:
            ref = refs.get(problem.id)
            if ref is None:
                continue
            for mutant in generate_mutants(
                ref.source, operators=operators, max_per_operator=max_per_operator
            ):
                tasks.append((problem, mutant))
    return tasks


def judge_task(verifier: CodeVerifier, problem, mutant, time_limit_ms: int) -> LabelledMutant:
    started = time.monotonic()
    effective = (
        problem
        if time_limit_ms <= 0 or problem.time_limit_ms <= time_limit_ms
        else dataclasses.replace(problem, time_limit_ms=time_limit_ms)
    )
    report = verifier.verify(mutant.source, effective, "python")
    elapsed_ms = int((time.monotonic() - started) * 1000)

    failure = report.first_failure
    return LabelledMutant(
        id=f"{problem.id}__{mutant.mutation.operator}__{mutant.site_index}",
        problem_id=problem.id,
        source=mutant.source,
        operator=mutant.mutation.operator,
        error_label=mutant.mutation.label,
        verdict=report.verdict.value,
        failed_case=failure.case_id if failure else "",
        detail=(failure.detail if failure else "")[:300],
        lineno=mutant.mutation.lineno,
        before=mutant.mutation.before,
        after=mutant.mutation.after,
        cascaded=bool(failure.cascaded) if failure else False,
        elapsed_ms=elapsed_ms,
        extras={
            "fingerprint": mutant.fingerprint,
            "passed": report.passed_count,
            "total": report.total_count,
            "cascaded_cases": sum(1 for c in report.cases if c.cascaded),
        },
    )


def summarise(records: list[LabelledMutant], args, elapsed: float) -> dict:
    valid = [r for r in records if r.verdict != Verdict.AC.value]
    equivalent = [r for r in records if r.verdict == Verdict.AC.value]

    buckets: dict[str, dict] = defaultdict(
        lambda: {"candidates": 0, "valid": 0, "equivalent": 0, "verdicts": Counter()}
    )
    labels: dict[str, str] = {}
    for rec in records:
        bucket = buckets[rec.operator]
        bucket["candidates"] += 1
        labels.setdefault(rec.operator, rec.error_label)
        if rec.verdict == Verdict.AC.value:
            bucket["equivalent"] += 1
        else:
            bucket["valid"] += 1
            bucket["verdicts"][rec.verdict] += 1

    rows = []
    for op, data in sorted(buckets.items(), key=lambda kv: -kv[1]["valid"]):
        rows.append(
            {
                "operator": op,
                "label": labels.get(op, ""),
                "candidates": data["candidates"],
                "valid": data["valid"],
                "equivalent": data["equivalent"],
                "equivalent_rate": round(data["equivalent"] / max(1, data["candidates"]), 4),
                "verdicts": dict(data["verdicts"]),
            }
        )

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "datasets": args.datasets or "all imported",
            "limit": args.limit,
            "max_per_operator": args.max_per_operator,
            "operators": args.operators or "all",
            "jobs": args.jobs,
            "time_limit_ms": args.time_limit_ms,
        },
        "totals": {
            "candidates": len(records),
            "valid": len(valid),
            "equivalent": len(equivalent),
            "equivalent_rate": round(len(equivalent) / max(1, len(records)), 4),
            "problems_covered": len({r.problem_id for r in valid}),
            "elapsed_s": round(elapsed, 2),
            "per_mutant_ms": round(elapsed / max(1, len(records)) * 1000, 1),
        },
        "verdict_distribution": dict(Counter(r.verdict for r in valid)),
        "error_label_distribution": dict(Counter(r.error_label for r in valid)),
        "operators": rows,
    }


def print_report(report: dict) -> None:
    totals = report["totals"]
    print("\n" + "=" * 78)
    print(f"候选变异体 {totals['candidates']} 个 → 有效（判题机证明失败）{totals['valid']} 个，"
          f"等价 {totals['equivalent']} 个")
    print(f"等价变异率 {totals['equivalent_rate'] * 100:.1f}%　"
          f"覆盖题目 {totals['problems_covered']} 道　"
          f"耗时 {totals['elapsed_s']}s（{totals['per_mutant_ms']} ms/个）")
    print("=" * 78)
    print(f"{'算子':<22}{'错因标签':<18}{'候选':>6}{'有效':>6}{'等价':>6}{'等价率':>9}")
    print("-" * 78)
    for row in report["operators"]:
        print(f"{row['operator']:<22}{row['label']:<18}{row['candidates']:>6}"
              f"{row['valid']:>6}{row['equivalent']:>6}{row['equivalent_rate'] * 100:>8.1f}%")
    print("-" * 78)
    print("判定分布：", report["verdict_distribution"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("datasets", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=None, help="每个数据集只取前 N 题")
    parser.add_argument("--max-per-operator", type=int, default=2,
                        help="每道题每个算子最多取几个变异位置")
    parser.add_argument("--operators", default=None, help="逗号分隔，默认全部")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--time-limit-ms", type=int, default=DEFAULT_MUTANT_TIME_LIMIT_MS,
                        help="变异体判题时限上限；0 表示沿用题目原时限")
    parser.add_argument("--out", default="eval/datasets/mutants.jsonl")
    parser.add_argument("--report", default="eval/results/mutation.json")
    args = parser.parse_args()

    names = args.datasets or [p.stem for p in sorted((ROOT / "problems").glob("*.jsonl"))]
    operators = args.operators.split(",") if args.operators else None

    print(f"生成变异体…（每题每算子最多 {args.max_per_operator} 个位置）")
    tasks = build_tasks(names, args.limit, args.max_per_operator, operators)
    if not tasks:
        print("没有生成任何变异体，请检查题库与参考解是否已导入")
        return 2
    print(f"共 {len(tasks)} 个候选变异体\n")

    sandbox = default_sandbox("local")
    # 变异体只需要回答「有没有全部通过」，不需要知道「哪几条挂了」，
    # 所以用快速判题器：一条用例失败就停。大题库上这是数量级的差别
    # （每题 8 条用例的 stdio 题，一个错误候选从 8 次进程降到 1–2 次）。
    verifier = CodeVerifier(sandbox=sandbox, stop_on_first_failure=True)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        records = list(
            pool.map(lambda t: judge_task(verifier, t[0], t[1], args.time_limit_ms), tasks)
        )
    elapsed = time.monotonic() - started

    report = summarise(records, args, elapsed)
    out_path = save_mutants(
        [r for r in records if r.verdict != Verdict.AC.value], ROOT / args.out
    )
    report_path = ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print_report(report)
    print(f"\n有效样本：{out_path.relative_to(ROOT)}（{report['totals']['valid']} 条）")
    print(f"统计报告：{report_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
