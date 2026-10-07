#!/usr/bin/env python3
"""掌握度建模与针对性出题的入口。

用法：
    # 看技能图谱：每项技能有多少道题能考察它
    python scripts/tutor.py --skills

    # 模拟一个学生的学习会话，打印每一步「为什么推荐这道题」
    python scripts/tutor.py --session --steps 12

    # 对照实验：靶向练习 vs 随机练习
    python scripts/tutor.py --experiment --learners 30 --budget 60
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_judge.tutoring import LearnerProfile, MasteryModel, Recommender, load_problem_skills  # noqa: E402
from code_judge.tutoring.models import Attempt  # noqa: E402
from code_judge.tutoring.simulate import SimulatedLearner, run_experiment, run_session  # noqa: E402
from code_judge.tutoring.skills import skill_summary  # noqa: E402

MUTANTS = ROOT / "eval" / "datasets" / "mutants.jsonl"


def load_all():
    if not MUTANTS.exists():
        print("找不到变异数据集，请先运行 scripts/mutate.py")
        raise SystemExit(2)
    return load_problem_skills(MUTANTS)


def show_skills(problem_skills, labels) -> int:
    rows = skill_summary(problem_skills, labels)
    print(f"题目 {len(problem_skills)} 道，技能 {len(rows)} 项\n")
    print(f"{'技能':<22}{'名称':<18}{'可考察题目数':>12}")
    print("-" * 54)
    for row in rows:
        print(f"{row['skill']:<22}{row['label']:<18}{row['problems']:>12}")
    print("\n说明：这个映射不是人工标注的，是从变异数据自动推导的——")
    print("      某道题能检出 k 类错误，就说明它的测试确实能考察 k 这项技能。")
    return 0


def run_session_demo(problem_skills, labels, args) -> int:
    skills = sorted(labels)
    weak = args.weak or "boundary_condition"
    if weak not in skills:
        weak = skills[0]
    error_prob = {s: 0.08 for s in skills}
    error_prob[weak] = 0.75
    learner = SimulatedLearner(user_id="demo", error_prob=error_prob, seed=args.seed)
    print(f"模拟学生：弱项是「{labels.get(weak, weak)}」（真实错误率 "
          f"{error_prob[weak]:.0%}），其它技能错误率 8%")
    print(f"学习模型：每练一次，该技能错误率 ×(1-25%)，边际收益递减\n")

    result, trace = run_session(
        learner, problem_skills, labels, condition="targeted", budget=args.steps, seed=args.seed
    )

    print(f"{'步':>3}  {'推荐理由':<52}{'结果':<8}{'犯的错'}")
    print("-" * 100)
    for item in trace:
        reason = item["reason"]
        reason = reason if len(reason) <= 50 else reason[:50] + "…"
        status = "AC 通过" if item["verdict"] == "AC" else "WA 未过"
        fault = labels.get(item["fault"], "") if item["fault"] else "-"
        print(f"{item['step']:>3}  {reason:<52}{status:<8}{fault}")

    print(f"\n共 {result.attempts} 次练习，其中 {result.target_hit_rate:.0%} 命中真实弱项")
    print(f"弱项错误率降到 {result.final_true_error_prob:.0%}"
          + (f"（第 {result.attempts_to_mastery} 次达标）" if result.attempts_to_mastery else "（未达标）"))
    print(f"系统判断的最弱技能：{labels.get(result.identified_weakest, result.identified_weakest)}"
          f"　{'与真实一致' if result.identified_correctly else '与真实不一致'}")
    return 0


def run_experiment_cli(problem_skills, labels, args) -> int:
    print(f"对照实验：{args.learners} 个模拟学员，预算 {args.budget} 次练习\n")
    started = time.monotonic()
    report = run_experiment(
        problem_skills, labels,
        learners=args.learners, budget=args.budget, seed=args.seed,
    )
    elapsed = time.monotonic() - started

    a, f, r = report["auto"], report["focus"], report["random"]
    print("=" * 92)
    print(f"{'指标':<26}{'先广后窄':>18}{'只练聚焦题':>18}{'随机出题':>18}")
    print("-" * 92)
    rows = [
        ("弱项识别准确率", f"{a['identify_accuracy']:.0%}", f"{f['identify_accuracy']:.0%}", f"{r['identify_accuracy']:.0%}"),
        ("认对弱项平均需要几步", _fmt(a["avg_attempts_to_identify"]), _fmt(f["avg_attempts_to_identify"]), _fmt(r["avg_attempts_to_identify"])),
        ("练习中命中弱项的比例", f"{a['weak_skill_hit_rate']:.0%}", f"{f['weak_skill_hit_rate']:.0%}", f"{r['weak_skill_hit_rate']:.0%}"),
        ("达到掌握阈值的比例", f"{a['mastery_reached_rate']:.0%}", f"{f['mastery_reached_rate']:.0%}", f"{r['mastery_reached_rate']:.0%}"),
        ("达标平均练习次数", _fmt(a["avg_attempts_to_mastery"]), _fmt(f["avg_attempts_to_mastery"]), _fmt(r["avg_attempts_to_mastery"])),
    ]
    for name, x, y, z in rows:
        print(f"{name:<26}{x:>18}{y:>18}{z:>18}")
    print("=" * 92)
    print(f"耗时 {elapsed:.1f}s")
    print("\n注意：这是模拟实验。学员按固定错误概率抽样、练习后错误率边际递减，")
    print("      它能说明机制按预期工作，不能说明对真实学生的效果——后者需要真实用户数据。")

    out = ROOT / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"报告：{out.relative_to(ROOT)}")
    return 0


def _fmt(value) -> str:
    return "-" if value is None else f"{value}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills", action="store_true", help="打印技能图谱")
    parser.add_argument("--session", action="store_true", help="模拟一次学习会话")
    parser.add_argument("--experiment", action="store_true", help="跑靶向 vs 随机对照实验")
    parser.add_argument("--weak", default=None, help="指定模拟学生的弱项技能")
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--learners", type=int, default=30)
    parser.add_argument("--budget", type=int, default=60)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--report", default="eval/results/tutoring.json")
    args = parser.parse_args()

    problem_skills, labels = load_all()
    if args.skills:
        return show_skills(problem_skills, labels)
    if args.session:
        return run_session_demo(problem_skills, labels, args)
    if args.experiment:
        return run_experiment_cli(problem_skills, labels, args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
