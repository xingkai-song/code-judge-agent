from __future__ import annotations

from pathlib import Path

from code_judge.diagnosis import Diagnoser
from code_judge.sandbox.local import LocalSandbox
from code_judge.tutoring.models import Attempt
from code_judge.tutoring.session import TutorSession
from code_judge.types import Problem, ProblemKind
from code_judge.types import TestCase as CaseSpec
from code_judge.verifier import CodeVerifier

LABELS = {
    "off_by_one_minus": "差一错误（偏小）",
    "comparison_operator": "比较符误用",
    "arithmetic_operator": "算术运算符误用",
}


def _problems() -> dict[str, Problem]:
    return {
        "P1": Problem(
            id="P1",
            title="恒等函数",
            statement="def solve(x):\n    \"\"\"返回 x。\"\"\"",
            kind=ProblemKind.ASSERT,
            entry_point="solve",
            time_limit_ms=3000,
            cases=[
                CaseSpec(id="c1", assert_code="assert solve(1) == 1"),
                CaseSpec(id="c2", assert_code="assert solve(2) == 2"),
            ],
        ),
        "P2": Problem(
            id="P2",
            title="两倍",
            statement="def twice(x):\n    \"\"\"返回 2x。\"\"\"",
            kind=ProblemKind.ASSERT,
            entry_point="twice",
            time_limit_ms=3000,
            cases=[CaseSpec(id="c1", assert_code="assert twice(3) == 6")],
        ),
    }


PROBLEM_SKILLS = {
    "P1": {"off_by_one_minus", "comparison_operator"},
    "P2": {"arithmetic_operator"},
}


def _session(tmp_path: Path) -> TutorSession:
    verifier = CodeVerifier(sandbox=LocalSandbox())
    return TutorSession(
        user_id="u1",
        problems=_problems(),
        problem_skills=PROBLEM_SKILLS,
        labels=LABELS,
        verifier=verifier,
        diagnoser=Diagnoser(verifier=verifier, max_repairs=1),
        profile_path=tmp_path / "profile.json",
    )


# --------------------------------------------------------------------------- #
# 提交与掌握度
# --------------------------------------------------------------------------- #


def test_success_credits_every_skill_the_problem_covers(tmp_path: Path):
    session = _session(tmp_path)
    outcome = session.submit("def solve(x):\n    return x\n", "P1")
    assert outcome.accepted
    state = session.mastery.update(session.profile)
    for skill in PROBLEM_SKILLS["P1"]:
        assert state[skill].opportunities == 1
        assert state[skill].errors == 0
        assert state[skill].mean == 2 / 3


def test_verified_failure_only_penalises_the_offending_skill(tmp_path: Path):
    session = _session(tmp_path)
    # 少算一次：return x + 1（正确是 x），诊断应定位到常量 1 偏小
    outcome = session.submit("def solve(x):\n    return x + 1\n", "P1")
    assert not outcome.accepted
    assert outcome.diagnosis is not None and outcome.diagnosis.verified
    assert outcome.attempt.fault_operator == "off_by_one_minus"

    state = session.mastery.update(session.profile)
    assert state["off_by_one_minus"].errors == 1
    assert state["off_by_one_minus"].mean < 0.5
    # 这道题也覆盖「比较符误用」，但这次失败不能算它的问题
    assert state["comparison_operator"].opportunities == 0


def test_unlocated_failure_updates_nothing(tmp_path: Path):
    """回归测试：诊断没定位到时，不能把这次失败当成「所有技能都做对了」。

    早期版本就是这么错的——学生失败一次，十项技能的掌握度反而全部上升。
    """
    session = _session(tmp_path)
    outcome = session.submit("def solve(x):\n    return None\n", "P1")
    assert not outcome.accepted
    assert outcome.diagnosis is not None and not outcome.diagnosis.verified

    state = session.mastery.update(session.profile)
    for skill in PROBLEM_SKILLS["P1"]:
        assert state[skill].opportunities == 0, "没有信息就不能更新掌握度"
        assert state[skill].mean == 0.5


def test_attempt_is_always_recorded_even_when_unlocated(tmp_path: Path):
    session = _session(tmp_path)
    session.submit("def solve(x):\n    return None\n", "P1")
    assert len(session.profile.attempts) == 1
    assert session.profile.attempts[0].verdict == "WA"
    assert session.profile.attempts[0].fault_operator == ""


# --------------------------------------------------------------------------- #
# 推荐与档案
# --------------------------------------------------------------------------- #


def test_recommendation_after_first_submission_avoids_the_same_problem(tmp_path: Path):
    session = _session(tmp_path)
    session.submit("def solve(x):\n    return x\n", "P1")
    recs = session.recommend(limit=3)
    assert recs
    # 首选必须是没有做过的题；做过的最多作为复测排在后面
    assert recs[0].problem_id != "P1"
    assert not recs[0].repeat
    repeats = [i for i, r in enumerate(recs) if r.repeat]
    fresh = [i for i, r in enumerate(recs) if not r.repeat]
    if repeats and fresh:
        assert max(fresh) < min(repeats), "新题必须排在复测前面"


def test_profile_persists_between_sessions(tmp_path: Path):
    first = _session(tmp_path)
    first.submit("def solve(x):\n    return x\n", "P1")

    second = _session(tmp_path)
    assert len(second.profile.attempts) == 1
    assert second.profile.user_id == "u1"
    recorded = second.mastery.update(second.profile)
    assert recorded["off_by_one_minus"].opportunities == 1


def test_starter_code_contains_the_signature(tmp_path: Path):
    session = _session(tmp_path)
    code = session.starter_code(session.problem("P1"))
    assert "def solve(x)" in code


def test_profile_report_mentions_the_weakest_skill(tmp_path: Path):
    session = _session(tmp_path)
    for _ in range(3):
        session.submit("def solve(x):\n    return x + 1\n", "P1")
    text = session.profile_report()
    assert "差一错误（偏小）" in text
    assert "最需要加强" in text
    assert "累计练习 3 次" in text


def test_submit_rejects_unknown_problem(tmp_path: Path):
    session = _session(tmp_path)
    try:
        session.submit("print(1)", "NOT_A_PROBLEM")
    except KeyError as exc:
        assert "NOT_A_PROBLEM" in str(exc)
    else:
        raise AssertionError("未知题目应该抛 KeyError")


def test_learner_profile_helpers():
    from code_judge.tutoring.models import LearnerProfile

    p = LearnerProfile("u")
    p.add(Attempt(problem_id="A", verdict="WA", fault_operator="x"))
    p.add(Attempt(problem_id="B", verdict="AC"))
    assert p.attempted_problem_ids == {"A", "B"}
    assert p.error_counts() == {"x": 1}
    assert p.attempts[1].solved
