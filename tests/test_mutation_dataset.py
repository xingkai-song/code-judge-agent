from __future__ import annotations

from pathlib import Path

from code_judge.mutation.dataset import LabelledMutant, load_mutants, save_mutants


def _sample(mutant_id: str = "P1__boundary_condition__3") -> LabelledMutant:
    return LabelledMutant(
        id=mutant_id,
        problem_id="P1",
        source="def f(n):\n    return n <= 1\n",
        operator="boundary_condition",
        error_label="边界条件错误",
        verdict="WA",
        failed_case="c2",
        detail="断言失败",
        lineno=2,
        before="n < 1",
        after="n <= 1",
        extras={"passed": 1, "total": 3},
    )


def test_roundtrip(tmp_path: Path):
    path = tmp_path / "mutants.jsonl"
    save_mutants([_sample()], path)
    loaded = load_mutants(path)
    assert len(loaded) == 1
    assert loaded[0] == _sample()


def test_unknown_fields_are_ignored(tmp_path: Path):
    path = tmp_path / "mutants.jsonl"
    save_mutants([_sample()], path)
    text = path.read_text(encoding="utf-8").replace(
        '"problem_id"', '"some_future_field": 1, "problem_id"'
    )
    path.write_text(text, encoding="utf-8")
    loaded = load_mutants(path)
    assert loaded[0].problem_id == "P1"
