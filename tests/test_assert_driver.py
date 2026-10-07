"""驱动脚本的生成必须永远是语法合法的。

这个测试是补出来的：模板里残留了给 ``str.format`` 用的双花括号 ``{{ }}``，
而实际用的是 ``str.replace``，于是生成的代码里字典变成了集合字面量，
运行时报 ``TypeError: unhashable type: 'dict'``，591 道题的参考解全军覆没。
生成代码的正确性只能靠「直接拿去编译一次」来保证。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from code_judge.assert_driver import RESULT_FILENAME, build_driver
from code_judge.sandbox.local import LocalSandbox
from code_judge.sandbox.base import ExecLimits, ExecRequest
from code_judge.types import Problem, ProblemKind
from code_judge.types import TestCase as CaseSpec  # 别叫 TestCase，pytest 会试图收集它
from code_judge.verifier import CodeVerifier


def test_generated_driver_is_valid_python():
    driver = build_driver("main.py", "solve", [("c1", "assert solve(1) == 1")])
    ast.parse(driver)  # 语法错误会在这里炸出来


def test_generated_driver_handles_multiline_and_empty_cases():
    multiline = "for i in range(3):\n    assert solve(i) == i"
    driver = build_driver(
        "main.py", "solve", [("c1", multiline), ("c2", "assert True")]
    )
    ast.parse(driver)
    assert 'case_id": "c2' in driver


def _assert_problem() -> Problem:
    return Problem(
        id="toy",
        kind=ProblemKind.ASSERT,
        entry_point="solve",
        time_limit_ms=5000,
        cases=[
            CaseSpec(id="c1", assert_code="assert solve(1) == 1"),
            CaseSpec(id="c2", assert_code="assert solve(2) == 4"),
            CaseSpec(id="c3", assert_code="assert solve(3) == 9"),
        ],
    )


def test_assert_problem_accepts_correct_solution():
    report = CodeVerifier(sandbox=LocalSandbox()).verify(
        "def solve(x):\n    return x * x\n", _assert_problem()
    )
    assert report.verdict.value == "AC"
    assert report.passed_count == 3


def test_assert_problem_pins_down_the_failing_case():
    # 只让 c2 挂：x==2 时返回 5
    report = CodeVerifier(sandbox=LocalSandbox()).verify(
        "def solve(x):\n    return 5 if x == 2 else x * x\n", _assert_problem()
    )
    assert report.verdict.value == "WA"
    assert report.first_failure.case_id == "c2"
    assert "断言失败" in report.first_failure.detail


def test_shared_state_between_statements_is_preserved():
    """回归测试：语句之间共享局部变量，拆成多个进程时会报 NameError。"""
    problem = Problem(
        id="shared",
        kind=ProblemKind.ASSERT,
        entry_point="solve",
        time_limit_ms=5000,
        cases=[
            CaseSpec(id="c1", assert_code="expected = 42"),
            CaseSpec(id="c2", assert_code="assert solve() == expected"),
        ],
    )
    report = CodeVerifier(sandbox=LocalSandbox()).verify("def solve():\n    return 42\n", problem)
    assert report.verdict.value == "AC", report.to_json()


def test_unexpected_error_is_reported_as_re_not_wa():
    problem = Problem(
        id="boom",
        kind=ProblemKind.ASSERT,
        entry_point="solve",
        time_limit_ms=5000,
        cases=[CaseSpec(id="c1", assert_code="assert solve('x') == 1")],
    )
    report = CodeVerifier(sandbox=LocalSandbox()).verify("def solve(x):\n    return x + 1\n", problem)
    assert report.verdict.value == "RE"
    assert "TypeError" in report.first_failure.detail


def test_missing_entry_point_is_reported_clearly():
    problem = Problem(
        id="missing",
        kind=ProblemKind.ASSERT,
        entry_point="solve",
        time_limit_ms=5000,
        cases=[CaseSpec(id="c1", assert_code="assert solve() == 1")],
    )
    report = CodeVerifier(sandbox=LocalSandbox()).verify("def other():\n    return 1\n", problem)
    assert report.verdict.value == "RE"
    assert "找不到入口函数" in report.first_failure.detail


def test_timeout_keeps_results_already_recorded():
    """跑不完的题也应该保留已经跑完的断言结果。"""
    problem = Problem(
        id="slow",
        kind=ProblemKind.ASSERT,
        entry_point="solve",
        time_limit_ms=600,
        cases=[
            CaseSpec(id="c1", assert_code="assert solve(1) == 1"),
            CaseSpec(id="c2", assert_code="import time; time.sleep(30)"),
            CaseSpec(id="c3", assert_code="assert solve(2) == 2"),
        ],
    )
    report = CodeVerifier(sandbox=LocalSandbox()).verify("def solve(x):\n    return x\n", problem)
    assert report.verdict.value in {"TLE", "RE"}
    assert report.cases[0].verdict.value == "AC", "超时前跑完的断言应当保留"


def test_assert_kind_rejects_non_python():
    import pytest

    problem = _assert_problem()
    with pytest.raises(ValueError, match="函数补全题"):
        CodeVerifier(sandbox=LocalSandbox()).verify("int main(){}", problem, "cpp")
