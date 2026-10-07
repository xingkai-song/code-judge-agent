#!/usr/bin/env python3
"""端到端演示：把 submissions/ 下的每份代码交给判题器，打印判定结果。

用法：
    python scripts/demo.py            # 自动选沙箱（有 Docker 用 Docker）
    python scripts/demo.py local      # 强制本地沙箱
    python scripts/demo.py docker     # 强制 Docker 沙箱
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_judge.problems import load_problems  # noqa: E402
from code_judge.sandbox import SandboxUnavailable, default_sandbox  # noqa: E402
from code_judge.sandbox.base import Sandbox  # noqa: E402
from code_judge.types import RunReport, Verdict  # noqa: E402
from code_judge.verifier import CodeVerifier  # noqa: E402

EXT_LANG = {".py": "python", ".cpp": "cpp"}
MARK = {
    Verdict.AC: "AC",
    Verdict.WA: "WA",
    Verdict.TLE: "TLE",
    Verdict.MLE: "MLE",
    Verdict.RE: "RE",
    Verdict.OLE: "OLE",
    Verdict.CE: "CE",
    Verdict.IE: "IE",
}


def pick_sandbox(prefer: str) -> Sandbox:
    try:
        sandbox = default_sandbox(prefer)
    except SandboxUnavailable as exc:
        print(f"！{exc}\n  回退到本地沙箱。\n")
        sandbox = default_sandbox("local")
    ok, reason = sandbox.available()
    print(f"沙箱：{sandbox.name}（{reason}）")
    print(f"能力：内存限制={'是' if sandbox.enforces_memory else '否'}　"
          f"网络隔离={'是' if sandbox.enforces_network_isolation else '否'}\n")
    return sandbox


def main() -> int:
    prefer = sys.argv[1] if len(sys.argv) > 1 else "auto"
    sandbox = pick_sandbox(prefer)
    verifier = CodeVerifier(sandbox=sandbox)
    problems = {p.id: p for p in load_problems(ROOT / "problems" / "samples")}

    rows: list[tuple[str, str, str, RunReport]] = []
    for problem_dir in sorted((ROOT / "submissions").iterdir()):
        if not problem_dir.is_dir():
            continue
        problem = problems.get(problem_dir.name)
        if problem is None:
            print(f"跳过 {problem_dir.name}：没有同名题目")
            continue
        for path in sorted(problem_dir.iterdir()):
            language = EXT_LANG.get(path.suffix)
            if language is None:
                continue
            report = verifier.verify_file(path, problem, language)
            rows.append((problem.id, path.name, language, report))

    header = (
        f"{'题目':<20}{'提交':<28}{'语言':<9}{'判定':<6}"
        f"{'用例':<8}{'编译ms':>8}{'执行ms':>8}"
    )
    print(header)
    print("-" * len(header))
    for problem_id, name, language, report in rows:
        cases = f"{report.passed_count}/{report.total_count}" if report.total_count else "-"
        run_ms = sum(c.time_ms for c in report.cases)
        print(
            f"{problem_id:<20}{name:<28}{language:<9}{MARK[report.verdict]:<6}"
            f"{cases:<8}{report.compile_ms:>8}{run_ms:>8}"
        )

    # 挑一个失败样本，展开看诊断层需要的细节
    sample = next((r for *_, r in rows if r.verdict is not Verdict.AC), None)
    if sample is not None:
        print("\n失败细节示例：")
        print(sample.summary())
        for case in sample.cases:
            if case.verdict.is_accepted:
                continue
            print(
                f"  用例 {case.case_id}  {case.verdict.value}"
                f"  exit={case.exit_code}  signal={case.signal_name or '-'}"
                f"  time={case.time_ms}ms  mem={case.memory_kb}KB"
            )
            if case.detail:
                print(f"    detail: {case.detail}")
            if case.stdout:
                print(f"    实际输出: {case.stdout.strip()[:60]!r}")
            if case.stderr:
                print(f"    错误输出: {case.stderr.strip().splitlines()[-1][:100]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
