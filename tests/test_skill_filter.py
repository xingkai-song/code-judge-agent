from __future__ import annotations

from code_judge.diagnosis.repairs import IRREVERSIBLE_OPERATORS
from code_judge.tutoring.skills import filter_tutorable


def test_irreversible_skills_are_removed():
    problem_skills = {
        "P1": {"boundary_condition", "loop_control"},
        "P2": {"loop_control"},          # 只剩不可逆技能 → 整道题退出推荐池
        "P3": {"arithmetic_operator"},
    }
    labels = {
        "boundary_condition": "边界条件错误",
        "loop_control": "循环控制错误",
        "arithmetic_operator": "算术运算符误用",
    }
    kept_problems, kept_labels = filter_tutorable(problem_skills, labels)

    assert "loop_control" not in kept_labels
    assert all(s not in IRREVERSIBLE_OPERATORS for skills in kept_problems.values() for s in skills)
    assert "P2" not in kept_problems, "只剩不可逆技能的题不该再被推荐"
    assert kept_problems["P1"] == {"boundary_condition"}
    assert set(kept_labels) == {"boundary_condition", "arithmetic_operator"}


def test_filter_is_non_destructive():
    problem_skills = {"P1": {"boundary_condition", "loop_control"}}
    labels = {"boundary_condition": "边界条件错误", "loop_control": "循环控制错误"}
    filter_tutorable(problem_skills, labels)
    # 原始数据不能被就地改掉，否则 --skills 视图会跟着缺项
    assert problem_skills["P1"] == {"boundary_condition", "loop_control"}
    assert "loop_control" in labels
