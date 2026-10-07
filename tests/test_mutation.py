from __future__ import annotations

import ast
from pathlib import Path

import pytest

from code_judge.mutation import OPERATORS, generate_mutants
from code_judge.problems import load_problems_jsonl
from code_judge.references import load_references
from code_judge.sandbox.local import LocalSandbox
from code_judge.types import Verdict
from code_judge.verifier import CodeVerifier

ROOT = Path(__file__).resolve().parents[1]


def _sources(mutants) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in mutants:
        out.setdefault(m.mutation.operator, []).append(m.source)
    return out


def test_arithmetic_mutation_changes_exactly_one_operator():
    source = "def f(n):\n    return n + 1\n"
    mutants = _sources(generate_mutants(source, operators=["arithmetic_operator"]))
    assert len(mutants["arithmetic_operator"]) == 1
    assert "- 1" in mutants["arithmetic_operator"][0]
    assert "+ 1" not in mutants["arithmetic_operator"][0]


def test_only_one_site_is_mutated_per_mutant():
    """两个算符的题，每个变异体只能动一个。"""
    source = "def f(a, b):\n    x = a + b\n    y = a + b\n    return x + y\n"
    mutants = generate_mutants(source, operators=["arithmetic_operator"], max_per_operator=5)
    assert len(mutants) == 3, [m.source for m in mutants]
    for m in mutants:
        # 变异体与原始源码逐行比较，只允许一行不同
        assert _differing_lines(source, m.source) == 1, m.source


def _differing_lines(a: str, b: str) -> int:
    """把两份源码各自规范化后逐行比较，返回不同的行数。

    变异体是用 ``ast.unparse`` 重新渲染的，格式和原文不同，所以先各自
    规范化一次再比，否则格式差异会淹没真正的改动。
    """
    import ast

    left = ast.unparse(ast.parse(a)).splitlines()
    right = ast.unparse(ast.parse(b)).splitlines()
    assert len(left) == len(right), f"结构变了：{len(left)} vs {len(right)}"
    return sum(1 for x, y in zip(left, right) if x != y)


def test_boundary_mutation_flips_strictness():
    source = "def f(xs):\n    return [x for x in xs if x < 10]\n"
    mutants = generate_mutants(source, operators=["boundary_condition"])
    assert len(mutants) == 1
    assert "<= 10" in mutants[0].source
    assert mutants[0].mutation.before == "x < 10"
    assert mutants[0].mutation.after == "x <= 10"


def test_all_mutants_are_valid_python():
    source = (
        "def f(n):\n"
        "    total = 0\n"
        "    for i in range(n):\n"
        "        if i % 2 == 0 and i > 0:\n"
        "            total += i\n"
        "        else:\n"
        "            continue\n"
        "    return total > 5\n"
    )
    mutants = generate_mutants(source, max_per_operator=3)
    assert mutants, "这份源码应该能产出多个变异体"
    # 变异体必须保留多行结构——曾经因为把换行压成空格导致全部非法
    assert any("\n" in m.source for m in mutants)
    for m in mutants:
        ast.parse(m.source)  # 变异后必须仍是合法代码
        assert m.source != source


def test_max_per_operator_is_respected():
    source = "def f(n):\n    return n + 0 + 0 + 0 + 0\n"
    mutants = generate_mutants(source, operators=["off_by_one_plus"], max_per_operator=2)
    assert len(mutants) == 2


def test_generation_is_reproducible():
    source = "def f(n):\n    return n + 0 + 0 + 0 + 0\n"
    first = [m.source for m in generate_mutants(source, operators=["off_by_one_plus"])]
    second = [m.source for m in generate_mutants(source, operators=["off_by_one_plus"])]
    assert first == second, "同样的输入必须得到同样的采样"


def test_broken_source_yields_no_mutants():
    assert generate_mutants("def f(:\n") == []


def test_every_operator_is_registered_with_a_label():
    for key, spec in OPERATORS.items():
        assert spec.key == key
        assert spec.label, f"{key} 缺少错因标签"
        assert spec.description, f"{key} 缺少说明"


@pytest.mark.skipif(
    not (ROOT / "solutions" / "humaneval.jsonl").exists(),
    reason="未导入 HumanEval，跳过集成测试",
)
class TestRealReferenceIntegration:
    """拿真实参考解跑一遍：生成的变异体必须真的被判错，标签才有意义。"""

    def test_mutants_of_real_references_are_detected_as_failures(self):
        problems = {p.id: p for p in load_problems_jsonl(ROOT / "problems" / "humaneval.jsonl")}
        refs = load_references(ROOT / "solutions" / "humaneval.jsonl")
        verifier = CodeVerifier(sandbox=LocalSandbox())

        checked = 0
        detected = 0
        for pid, ref in list(refs.items())[:5]:
            problem = problems[pid]
            for mutant in generate_mutants(ref.source, max_per_operator=1)[:3]:
                report = verifier.verify(mutant.source, problem, "python")
                checked += 1
                if report.verdict is not Verdict.AC:
                    detected += 1
                else:
                    # 等价变异体是允许存在的，只是不算有效样本
                    assert report.passed_count == report.total_count

        assert checked > 0, "应该至少生成一些变异体"
        assert detected > 0, "至少要有一部分变异体被判题机检出"
