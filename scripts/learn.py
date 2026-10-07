#!/usr/bin/env python3
"""交互式陪练入口——给人用的那一层。

用法：
    python scripts/learn.py --user alice          # 进入交互会话
    python scripts/learn.py --user alice --demo   # 自动跑一遍演示，不用手敲

会话里能做什么：
    next             看推荐的题目
    show <序号>      看题面
    start <序号>     生成起步文件，用编辑器写代码
    submit <文件>    提交并拿到诊断
    profile          看掌握度
    help / quit

答题记录存在 workspace/<user>/profile.json，代码文件也在同一个目录里，
可以直接发给同学用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_judge.diagnosis import Diagnoser  # noqa: E402
from code_judge.problems import load_library  # noqa: E402
from code_judge.references import load_references  # noqa: E402
from code_judge.mutation.engine import generate_mutants  # noqa: E402
from code_judge.sandbox import default_sandbox  # noqa: E402
from code_judge.tutoring.session import TutorSession  # noqa: E402
from code_judge.tutoring.skills import filter_tutorable, load_problem_skills  # noqa: E402
from code_judge.verifier import CodeVerifier  # noqa: E402

HELP = """\
命令：
  next              看推荐题目
  show <序号>       查看题面
  start <序号>      生成起步文件（写到 workspace/<user>/ 下）
  submit <文件>     提交代码，失败会给出诊断和修改建议
  profile           查看掌握度
  help              显示本帮助
  quit              退出
"""


def build_session(user: str) -> TutorSession:
    mutants = ROOT / "eval" / "datasets" / "mutants.jsonl"
    if not mutants.exists():
        print("找不到变异数据集，请先运行：python scripts/mutate.py --jobs 6")
        raise SystemExit(2)

    problems = load_library(ROOT / "problems")
    problem_skills, labels = load_problem_skills(mutants)
    # 不能诊断的技能不出题（例如「删掉 break」不可逆，系统定位不到）
    problem_skills, labels = filter_tutorable(problem_skills, labels)
    # 只保留题库里真实存在的题目，避免推荐到已被清理的题
    problem_skills = {k: v for k, v in problem_skills.items() if k in problems}

    workdir = ROOT / "workspace" / user
    workdir.mkdir(parents=True, exist_ok=True)

    verifier = CodeVerifier(sandbox=default_sandbox("local"))
    return TutorSession(
        user_id=user,
        problems=problems,
        problem_skills=problem_skills,
        labels=labels,
        verifier=verifier,
        diagnoser=Diagnoser(verifier=verifier, max_repairs=2),
        profile_path=workdir / "profile.json",
    )


def print_recommendations(session: TutorSession, limit: int = 3) -> list:
    cached = session.recommend(limit=limit)
    if not cached:
        print("暂时没有可推荐的题目。")
        return cached
    print("\n推荐练习：")
    for i, rec in enumerate(cached, 1):
        print(f"  {i}. [{rec.problem_id}]  针对「{rec.target_label}」")
        print(f"     {rec.reason}")
    print()
    return cached


def show_problem(session: TutorSession, problem_id: str) -> None:
    problem = session.problem(problem_id)
    if problem is None:
        print(f"找不到题目 {problem_id}")
        return
    print(f"\n===== {problem.id}　{problem.title} =====")
    print(problem.statement.strip() if problem.statement else "（无题面）")
    if problem.kind.value == "assert":
        print(f"\n要求实现函数：{problem.entry_point}")
    print(f"限制：{problem.time_limit_ms} ms / {problem.memory_limit_mb} MB"
          f"　测试用例 {len(problem.cases)} 条")
    skills = sorted(session.problem_skills.get(problem.id, []))
    shown = "、".join(session.labels.get(s, s) for s in skills) if skills else "（未标注）"
    print(f"考察技能：{shown}")
    print()


def start_problem(session: TutorSession, rec) -> Path | None:
    problem = session.problem(rec.problem_id)
    if problem is None:
        print(f"找不到题目 {rec.problem_id}")
        return None
    workdir = session.profile_path.parent if session.profile_path else ROOT / "workspace"
    path = workdir / f"{problem.id}.py"
    if path.exists():
        print(f"文件已存在，直接编辑它：{path}")
        return path
    path.write_text(session.starter_code(problem), encoding="utf-8")
    print(f"已生成起步文件：{path}")
    print("用任意编辑器打开它写代码，写完回来执行：submit " + str(path.relative_to(ROOT)))
    return path


def submit(session: TutorSession, file_arg: str) -> None:
    path = Path(file_arg)
    if not path.is_absolute():
        candidate = ROOT / path
        path = candidate if candidate.exists() else Path.cwd() / path
    if not path.exists():
        print(f"找不到文件：{file_arg}")
        return

    problem_id = path.stem
    if session.problem(problem_id) is None:
        print(f"文件名 {path.name} 不是题库里的题目 id，无法判断提交的是哪道题。")
        print("请用 start 生成的文件名（例如 HumanEval_3.py），或用 --problem 指定。")
        return

    print(f"判题中…（第一次会稍慢）")
    outcome = session.submit(path.read_text(encoding="utf-8"), problem_id)

    print(f"\n结果：{outcome.verdict}　通过 {outcome.passed}/{outcome.total} 条用例")
    if outcome.explanation:
        print()
        print(outcome.explanation)
    if outcome.accepted:
        print()
        print_recommendations(session, outcome.next_recommendations)
    else:
        print("\n改完再提交一次即可，我会重新判。")


def run_demo(session: TutorSession) -> int:
    """无人值守演示：自动选中第一道推荐题，交一份带典型错误的代码，看完整闭环。"""
    print("=== 演示模式：不需要手敲命令 ===\n")
    recs = session.recommend(limit=1)
    if not recs:
        print("没有可推荐的题目")
        return 2
    rec = recs[0]
    print(f"推荐题目：{rec.problem_id}　针对「{rec.target_label}」")
    print(f"理由：{rec.reason}\n")

    source = _demo_submission(session, rec)
    print("--- 提交一份带有该类典型错误的代码（取参考解注入该错因得到）---")
    outcome = session.submit(source, rec.problem_id)
    print(f"判定：{outcome.verdict}　通过 {outcome.passed}/{outcome.total}")
    print()
    print(outcome.explanation)
    print()
    print(session.profile_report())
    return 0


def _demo_submission(session: TutorSession, rec) -> str:
    """演示用提交：拿参考解注入目标错因，模拟一个真实学生会犯的错。"""
    refs_path = ROOT / "solutions" / "humaneval.jsonl"
    if refs_path.exists():
        refs = load_references(refs_path)
        ref = refs.get(rec.problem_id)
        if ref is not None:
            mutants = generate_mutants(
                ref.source, operators=[rec.target_skill], max_per_operator=1
            )
            if mutants:
                return mutants[0].source
    # 没有参考解就退回「函数体写 pass」
    problem = session.problem(rec.problem_id)
    return session.starter_code(problem) if problem else "def solution():\n    pass\n"


def repl(session: TutorSession) -> int:
    print(f"你好 {session.user_id}。这是你的 Agent 陪练。")
    print(f"答题记录会存在 {session.profile_path}\n")
    print(HELP)
    cached = print_recommendations(session)

    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw:
            continue
        cmd, _, rest = raw.partition(" ")
        cmd, rest = cmd.lower(), rest.strip()

        if cmd in ("quit", "exit", "q"):
            return 0
        if cmd in ("help", "h", "?"):
            print(HELP)
        elif cmd in ("next", "n"):
            cached = print_recommendations(session)
        elif cmd == "show":
            problem_id = _resolve(rest, cached)
            if problem_id:
                show_problem(session, problem_id)
        elif cmd == "start":
            problem_id = _resolve(rest, cached)
            rec = next((r for r in cached if r.problem_id == problem_id), None)
            if rec:
                start_problem(session, rec)
        elif cmd == "submit":
            if not rest:
                print("用法：submit <文件>")
            else:
                submit(session, rest)
        elif cmd in ("profile", "p"):
            print()
            print(session.profile_report())
            print()
        else:
            print(f"未知命令：{cmd}。输入 help 看可用命令。")


def _resolve(token: str, cached: list) -> str | None:
    """支持用序号选推荐题，也支持直接写题目 id。"""
    if not token:
        print("请指定序号或题目 id")
        return None
    if token.isdigit():
        idx = int(token) - 1
        if 0 <= idx < len(cached):
            return cached[idx].problem_id
        print(f"序号超出范围（当前有 {len(cached)} 条推荐）")
        return None
    return token


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="default", help="学习者标识，决定记录存放目录")
    parser.add_argument("--demo", action="store_true", help="自动跑一遍演示")
    args = parser.parse_args()

    session = build_session(args.user)
    return run_demo(session) if args.demo else repl(session)


if __name__ == "__main__":
    raise SystemExit(main())
