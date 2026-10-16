"""失败模式分析的单元测试。

这一层是「定位不到时说点有用的」：只从判题结果里读事实，不做推测。
测试全部用构造出来的 RunReport，不碰题库和沙箱，跑得很快。
"""

from __future__ import annotations

from code_judge.diagnosis.patterns import analyze
from code_judge.types import CaseResult, Problem, RunReport, Verdict
from code_judge.types import TestCase as CaseSpec


def _case(case_id: str, verdict: Verdict, expected: str = "", stdout: str = "",
          time_ms: int = 10, stderr: str = "") -> CaseResult:
    return CaseResult(
        case_id=case_id,
        verdict=verdict,
        expected_stdout=expected,
        stdout=stdout,
        time_ms=time_ms,
        stderr=stderr,
    )


def _problem() -> Problem:
    # Problem 要求至少有一条用例（否则判题无意义），这里给一条占位的
    return Problem(
        id="P",
        time_limit_ms=2000,
        cases=[CaseSpec(id="c1", stdin="1\n", expected_stdout="1\n")],
    )


def test_all_cases_failing_points_at_the_overall_approach():
    report = RunReport(
        verdict=Verdict.WA,
        cases=[
            _case("c1", Verdict.WA, "1", "13"),
            _case("c2", Verdict.WA, "0", "48"),
        ],
    )
    notes = analyze(report, _problem())
    assert any("一条都没过" in n for n in notes)


def test_partial_failure_with_shared_expected_output_flags_a_missing_branch():
    """失败用例的期望值完全一致、且和通过的都不同 → 有一整类情况没处理。"""
    report = RunReport(
        verdict=Verdict.WA,
        cases=[
            _case("c1", Verdict.WA, "1", ""),      # 期望 1 的全挂
            _case("c2", Verdict.AC, "0", "0"),
            _case("c3", Verdict.WA, "1", ""),
            _case("c4", Verdict.AC, "0", "0"),
        ],
    )
    notes = analyze(report, _problem())
    assert any("一整类情况" in n for n in notes)
    assert any("4 条用例里 2 条通过" in n for n in notes)


def test_timeout_with_some_passing_points_at_efficiency():
    report = RunReport(
        verdict=Verdict.TLE,
        cases=[
            _case("c1", Verdict.AC, "3", "3"),
            _case("c2", Verdict.TLE, "5", "", time_ms=2000),
        ],
    )
    notes = analyze(report, _problem())
    assert any("超时" in n and "时限 2000 ms" in n for n in notes)


def test_runtime_error_message_is_passed_through():
    report = RunReport(
        verdict=Verdict.RE,
        cases=[_case("c1", Verdict.RE, "3", "", stderr="traceback...\nIndexError: list index out of range")],
    )
    notes = analyze(report, _problem())
    assert any("IndexError" in n for n in notes)


def test_no_notes_when_everything_passes():
    report = RunReport(
        verdict=Verdict.AC,
        cases=[_case("c1", Verdict.AC, "3", "3"), _case("c2", Verdict.AC, "5", "5")],
    )
    assert analyze(report, _problem()) == []


def test_empty_report_is_safe():
    assert analyze(RunReport(verdict=Verdict.IE), _problem()) == []
