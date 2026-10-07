#!/usr/bin/env python3
"""诊断闭环的入口。

两种用法：

1. **诊断一份提交**
       python scripts/diagnose.py --file submissions/sum_two/wrong_answer.py --problem sum_two

2. **在变异数据集上评估闭环**
       python scripts/diagnose.py --evaluate --sample 200 --jobs 6

   变异数据集里每条样本的错因是已知的（注入时就确定了），所以可以直接算准确率：
   系统给出的错因类型和行号，是不是和当初注入的一致。

   注意评估口径：修复候选用的算子和当初注入用的算子是同一套，所以这属于
   **同分布评估**，衡量的是「闭环机制本身跑得通」。想看更接近真实情况的泛化能力，
   用 ``--exclude-operators`` 把某类算子从修复候选里去掉，模拟「没见过这个错因」。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_judge.diagnosis import Diagnoser, explain  # noqa: E402
from code_judge.diagnosis.repairs import IRREVERSIBLE_OPERATORS, PREFERRED_ORDER  # noqa: E402
from code_judge.mutation.dataset import load_mutants  # noqa: E402
from code_judge.problems import load_problems_jsonl  # noqa: E402
from code_judge.sandbox import default_sandbox  # noqa: E402
from code_judge.verifier import CodeVerifier  # noqa: E402


def load_problem_index() -> dict:
    index = {}
    # 示例题是目录里的单个 JSON，数据集是 JSONL
    samples_dir = ROOT / "problems" / "samples"
    if samples_dir.is_dir():
        from code_judge.problems import load_problems

        for problem in load_problems(samples_dir):
            index[problem.id] = problem
    for path in sorted((ROOT / "problems").glob("*.jsonl")):
        for problem in load_problems_jsonl(path):
            index[problem.id] = problem
    return index


def diagnose_one(diagnoser: Diagnoser, problem, source: str) -> dict:
    diagnosis = diagnoser.diagnose(source, problem)
    first = diagnosis.repairs[0] if diagnosis.repairs else None
    return {
        "problem_id": diagnosis.problem_id,
        "verified": diagnosis.verified,
        "predicted_operator": diagnosis.operator,
        "predicted_label": diagnosis.fault_label,
        "predicted_lineno": diagnosis.lineno,
        "repair_before": first.before if first else "",
        "repair_after": first.after if first else "",
        "candidates_tried": diagnosis.candidates_tried,
        "candidates_total": diagnosis.candidates_total,
        "elapsed_ms": diagnosis.elapsed_ms,
        "repairs": len(diagnosis.repairs),
    }


def evaluate(args) -> int:
    dataset_path = ROOT / "eval" / "datasets" / "mutants.jsonl"
    if not dataset_path.exists():
        print("找不到变异数据集，请先运行 scripts/mutate.py")
        return 2

    problems = load_problem_index()
    mutants = [m for m in load_mutants(dataset_path) if m.problem_id in problems]
    if args.sample and args.sample < len(mutants):
        mutants = random.Random(args.seed).sample(mutants, args.sample)
    print(f"样本 {len(mutants)} 条，来自 {len({m.problem_id for m in mutants})} 道题\n")

    excluded = set((args.exclude_operators or "").split(",")) if args.exclude_operators else set()
    if excluded:
        print(f"！修复候选中排除算子：{sorted(excluded)}（模拟没见过这类错因）\n")

    verifier = CodeVerifier(sandbox=default_sandbox("local"))
    diagnoser = Diagnoser(verifier=verifier, max_repairs=1, exclude_operators=excluded)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(
            pool.map(lambda m: diagnose_one(diagnoser, problems[m.problem_id], m.source), mutants)
        )
    elapsed = time.monotonic() - started

    for record, mutant in zip(results, mutants):
        record["expected_operator"] = mutant.operator
        record["expected_label"] = mutant.error_label
        record["expected_lineno"] = mutant.lineno
        record["expected_before"] = mutant.before
        record["expected_after"] = mutant.after

    report = summarise(results, mutants, excluded, elapsed, args)
    out_path = ROOT / args.report
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\n报告：{out_path.relative_to(ROOT)}")
    return 0


def summarise(results, mutants, excluded, elapsed, args) -> dict:
    total = len(results)
    verified = [r for r in results if r["verified"]]
    type_ok = [r for r in verified if _same_type(r)]
    change_ok = [r for r in verified if _change_match(r)]
    both_ok = [r for r in verified if _same_type(r) and _change_match(r)]
    line_ok = [r for r in verified if r["predicted_lineno"] == r["expected_lineno"]]

    per_operator: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "verified": 0, "type_ok": 0, "change_ok": 0, "line_ok": 0}
    )
    for r in results:
        bucket = per_operator[r["expected_operator"]]
        bucket["n"] += 1
        if r["verified"]:
            bucket["verified"] += 1
            if _same_type(r):
                bucket["type_ok"] += 1
            if _change_match(r):
                bucket["change_ok"] += 1
            if r["predicted_lineno"] == r["expected_lineno"]:
                bucket["line_ok"] += 1

    confusions = Counter(
        (r["expected_operator"], r["predicted_operator"])
        for r in verified
        if not _same_type(r)
    )
    unresolved = Counter(r["expected_operator"] for r in results if not r["verified"])

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "sample": args.sample,
            "seed": args.seed,
            "jobs": args.jobs,
            "excluded_operators": sorted(excluded),
        },
        "totals": {
            "samples": total,
            "verified": len(verified),
            "verified_rate": round(len(verified) / max(1, total), 4),
            "type_accuracy": round(len(type_ok) / max(1, len(verified)), 4),
            "change_accuracy": round(len(change_ok) / max(1, len(verified)), 4),
            "line_accuracy_reference": round(len(line_ok) / max(1, len(verified)), 4),
            "exact_match": round(len(both_ok) / max(1, len(verified)), 4),
            "end_to_end": round(len(both_ok) / max(1, total), 4),
            "avg_candidates_tried": round(
                sum(r["candidates_tried"] for r in results) / max(1, total), 1
            ),
            "avg_ms": round(elapsed / max(1, total) * 1000, 1),
            "elapsed_s": round(elapsed, 2),
        },
        "per_operator": {
            k: {
                **v,
                "verified_rate": round(v["verified"] / max(1, v["n"]), 4),
                "type_accuracy": round(v["type_ok"] / max(1, v["verified"]), 4),
                "change_accuracy": round(v["change_ok"] / max(1, v["verified"]), 4),
            }
            for k, v in sorted(per_operator.items(), key=lambda kv: -kv[1]["n"])
        },
        "confusions": [{"expected": e, "predicted": p, "count": c} for (e, p), c in confusions.most_common(10)],
        "unresolved_by_operator": dict(unresolved),
    }


def _same_type(record: dict) -> bool:
    """同一类错因可能有多个算子方向（例如差一偏大/偏小），算作同类。"""
    predicted = record["predicted_operator"]
    expected = record["expected_operator"]
    if predicted == expected:
        return True
    return _family(predicted) == _family(expected)


def _change_match(record: dict) -> bool:
    """找到的修复是不是恰好把注入的改动反向撤销了。

    为什么不直接比行号：变异体是用 ``ast.unparse`` 重新渲染的，**行号会被重排**，
    数据集里记的行号来自参考解、诊断报的行号来自变异体，两者根本不在同一套编号上。
    比「改动内容」才是语义等价、编号无关的口径：
    注入的是 ``a + b → a - b``，那么正确的修复就应该是 ``a - b → a + b``。

    真实的诊断场景里，报出的行号就是学生文件里的行号（同一份文件），不受这个影响。
    """
    if not record.get("repair_before"):
        return False
    return (
        record["repair_before"] == record["expected_after"]
        and record["repair_after"] == record["expected_before"]
    )


def _family(operator: str) -> str:
    return operator.replace("_plus", "").replace("_minus", "").replace("_drop", "")


def print_report(report: dict) -> None:
    t = report["totals"]
    print("\n" + "=" * 82)
    print(f"样本 {t['samples']} 条　定位成功 {t['verified']} 条（{t['verified_rate'] * 100:.1f}%）")
    print("=" * 82)
    print(f"错因类型准确率　{t['type_accuracy'] * 100:.1f}%（在定位成功的样本上）")
    print(f"修复改动准确率　{t['change_accuracy'] * 100:.1f}%（找到的修复恰好撤销了注入的改动）")
    print(f"类型+改动全对　　{t['exact_match'] * 100:.1f}%")
    print(f"端到端（含未定位）{t['end_to_end'] * 100:.1f}%")
    print(f"（行号准确率 {t['line_accuracy_reference'] * 100:.1f}%，仅供参考——"
          "变异体重编号导致两个行号不在同一套编号上）")
    print(f"平均尝试候选　　{t['avg_candidates_tried']} 个　单条耗时 {t['avg_ms']} ms"
          f"　总耗时 {t['elapsed_s']}s")
    print("-" * 82)
    print(f"{'注入的错因':<18}{'样本':>6}{'定位成功':>9}{'类型准确':>9}{'改动准确':>9}")
    print("-" * 82)
    for op, row in report["per_operator"].items():
        print(f"{op:<18}{row['n']:>6}{row['verified_rate'] * 100:>8.1f}%"
              f"{row['type_accuracy'] * 100:>8.1f}%{row['change_accuracy'] * 100:>8.1f}%")
    if report["confusions"]:
        print("-" * 82)
        print("类型判断错误的前几种：")
        for item in report["confusions"][:5]:
            print(f"  注入 {item['expected']:<22} → 判成 {item['predicted']:<22} {item['count']} 次")
    if report["unresolved_by_operator"]:
        print("-" * 82)
        print("未能定位的样本按注入错因分布：", report["unresolved_by_operator"])
    if t["type_accuracy"] < 1.0:
        print("-" * 82)
        print(
            "说明：类型没判对的那部分，绝大多数不是「定位错了」，而是系统找到的\n"
            "      是**另一种也能让全部测试通过的改法**——每条报告的修复都真跑过。\n"
            "      修复不唯一，说明这套测试用例不足以区分这两种改法，\n"
            "      和等价变异率是同一个现象的两面。"
        )


def diagnose_file(args) -> int:
    problems = load_problem_index()
    if args.problem:
        problem = problems.get(args.problem)
    else:
        problem = None
    if problem is None:
        print(f"找不到题目 {args.problem!r}")
        return 2

    source = Path(args.file).read_text(encoding="utf-8")
    diagnoser = Diagnoser(verifier=CodeVerifier(sandbox=default_sandbox("local")), max_repairs=3)
    diagnosis = diagnoser.diagnose(source, problem)
    print(explain(diagnosis))
    if args.json:
        print("\n" + diagnosis.to_json())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="要诊断的提交文件")
    parser.add_argument("--problem", help="题目 id，例如 sum_two 或 HumanEval_3")
    parser.add_argument("--json", action="store_true", help="同时打印完整 JSON")
    parser.add_argument("--evaluate", action="store_true", help="在变异数据集上评估闭环")
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--exclude-operators", default=None,
                        help="从修复候选中排除的算子，逗号分隔（模拟没见过这类错因）")
    parser.add_argument("--report", default="eval/results/diagnosis.json")
    args = parser.parse_args()

    if args.evaluate:
        return evaluate(args)
    if args.file:
        return diagnose_file(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
