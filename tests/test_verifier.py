from __future__ import annotations

from pathlib import Path

import pytest

from code_judge.languages import available_languages
from code_judge.problems import load_problem
from code_judge.sandbox.local import LocalSandbox
from code_judge.types import Verdict
from code_judge.verifier import CodeVerifier

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "problems" / "samples"
SUBMISSIONS = ROOT / "submissions"


@pytest.fixture(scope="module")
def verifier() -> CodeVerifier:
    return CodeVerifier(sandbox=LocalSandbox())


def judge(verifier: CodeVerifier, problem_name: str, submission: str) -> "object":
    problem = load_problem(SAMPLES / f"{problem_name}.json")
    path = SUBMISSIONS / problem_name / submission
    language = "cpp" if path.suffix == ".cpp" else "python"
    return verifier.verify_file(path, problem, language)


def test_correct_python_is_accepted(verifier):
    report = judge(verifier, "sum_two", "correct.py")
    assert report.verdict is Verdict.AC
    assert report.passed_count == report.total_count == 3


def test_wrong_answer_is_detected(verifier):
    report = judge(verifier, "sum_two", "wrong_answer.py")
    assert report.verdict is Verdict.WA
    assert report.passed_count == 0
    assert "第 1 项" in report.first_failure.detail


def test_runtime_error_is_detected(verifier):
    report = judge(verifier, "sum_two", "runtime_error.py")
    assert report.verdict is Verdict.RE
    assert "ZeroDivisionError" in report.first_failure.stderr


def test_infinite_loop_is_time_limited(verifier):
    report = judge(verifier, "sum_two", "timeout.py")
    assert report.verdict is Verdict.TLE


def test_partial_pass_keeps_every_case(verifier):
    """错在边界上的提交应该只挂掉那一条，其余用例结果要保留下来给诊断层用。"""
    report = judge(verifier, "max_subarray", "wrong_answer_init_zero.py")
    assert report.verdict is Verdict.WA
    assert report.passed_count == 3 and report.total_count == 4
    assert report.first_failure.case_id == "c2"


def test_case_sensitivity_bug_is_caught(verifier):
    report = judge(verifier, "count_unique_words", "case_sensitive.py")
    assert report.verdict is Verdict.WA
    assert report.first_failure.case_id == "c2"


@pytest.mark.skipif("cpp" not in available_languages(), reason="本机没有 C++ 编译器")
class TestCpp:
    def test_correct_cpp_is_accepted(self, verifier):
        report = judge(verifier, "sum_two", "correct.cpp")
        assert report.verdict is Verdict.AC
        assert report.compile_ms >= 0

    def test_integer_overflow_is_caught(self, verifier):
        report = judge(verifier, "sum_two", "overflow.cpp")
        assert report.verdict is Verdict.WA
        assert report.first_failure.case_id == "c3"

    def test_compile_error_is_reported(self, verifier):
        report = judge(verifier, "sum_two", "compile_error.cpp")
        assert report.verdict is Verdict.CE
        assert report.compile_error.strip()


def test_report_serializes_to_json(verifier):
    report = judge(verifier, "sum_two", "correct.py")
    payload = report.to_dict()
    assert payload["verdict"] == "AC"
    assert payload["sandbox"] == "local"
    assert len(payload["cases"]) == 3
    assert '"verdict": "AC"' in report.to_json()


def test_problem_requires_cases():
    with pytest.raises(ValueError):
        from code_judge.types import Problem

        Problem(id="empty")
