from __future__ import annotations

import ast
from pathlib import Path

from code_judge.diagnosis import Diagnoser, explain, propose_repairs
from code_judge.diagnosis.repairs import IRREVERSIBLE_OPERATORS
from code_judge.problems import load_problem
from code_judge.sandbox.local import LocalSandbox
from code_judge.types import Verdict
from code_judge.verifier import CodeVerifier

ROOT = Path(__file__).resolve().parents[1]


def _problem(name: str):
    return load_problem(ROOT / "problems" / "samples" / f"{name}.json")


def _submission(problem: str, name: str) -> str:
    return (ROOT / "submissions" / problem / name).read_text(encoding="utf-8")


def _diagnoser() -> Diagnoser:
    return Diagnoser(verifier=CodeVerifier(sandbox=LocalSandbox()), max_repairs=2)


def test_correct_submission_reports_no_fault():
    diagnosis = _diagnoser().diagnose(_submission("sum_two", "correct.py"), _problem("sum_two"))
    assert diagnosis.submitted_verdict == "AC"
    assert diagnosis.fault_label == "无错误"
    assert "没有发现错误" in explain(diagnosis)


def test_arithmetic_fault_is_located_and_verified():
    diagnosis = _diagnoser().diagnose(
        _submission("sum_two", "wrong_answer.py"), _problem("sum_two")
    )
    assert diagnosis.verified
    assert diagnosis.operator == "arithmetic_operator"
    assert diagnosis.fault_label == "算术运算符误用"
    repair = diagnosis.repairs[0]
    assert repair.before.replace(" ", "") == "a-b"
    assert repair.after.replace(" ", "") == "a+b"


def test_every_reported_repair_really_passes_the_judge():
    """这是闭环最核心的保证：报告里给出修复，必须真的被判题机跑通。"""
    diagnosis = _diagnoser().diagnose(
        _submission("sum_two", "wrong_answer.py"), _problem("sum_two")
    )
    verifier = CodeVerifier(sandbox=LocalSandbox())
    assert diagnosis.repairs, "应该至少找到一个修复"
    for repair in diagnosis.repairs:
        report = verifier.verify(repair.source, _problem("sum_two"), "python")
        assert report.verdict is Verdict.AC, f"{repair.operator} 的修复并没有真的通过"
        assert repair.passed == repair.total == report.total_count


def test_structural_error_is_reported_as_unlocated_not_guessed():
    """结构性问题（不是单点改动能修的）必须如实返回未定位。"""
    diagnosis = _diagnoser().diagnose(
        _submission("count_unique_words", "case_sensitive.py"),
        _problem("count_unique_words"),
    )
    assert not diagnosis.verified
    assert not diagnosis.repairs
    text = explain(diagnosis)
    assert "未能定位" in text
    assert "不做猜测" in text or "不做任何猜测" in text


def test_explanation_carries_evidence_from_the_failure():
    diagnosis = _diagnoser().diagnose(
        _submission("sum_two", "wrong_answer.py"), _problem("sum_two")
    )
    text = explain(diagnosis)
    assert "已通过判题机验证" in text
    assert diagnosis.symptom["failed_case"] in text
    assert "期望" in text and "实际" in text


def test_repair_candidates_are_valid_python():
    source = _submission("max_subarray", "correct.py")
    for candidate in propose_repairs(source):
        ast.parse(candidate.source)


def test_irreversible_operator_is_not_offered_as_a_repair():
    """删掉 break 这种不可逆的改动不该出现在修复候选里。"""
    source = "def f(n):\n    for i in range(n):\n        if i > 2:\n            break\n    return i\n"
    operators = {c.operator for c in propose_repairs(source)}
    assert not (operators & IRREVERSIBLE_OPERATORS)


def test_diagnosis_serialises_to_json():
    diagnosis = _diagnoser().diagnose(
        _submission("sum_two", "wrong_answer.py"), _problem("sum_two")
    )
    payload = diagnosis.to_dict()
    assert payload["verified"] is True
    assert payload["fault_label"]
    assert '"verified": true' in diagnosis.to_json()
