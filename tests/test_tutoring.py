from __future__ import annotations

from pathlib import Path

from code_judge.tutoring import LearnerProfile, MasteryModel, Recommender
from code_judge.tutoring.models import Attempt
from code_judge.tutoring.simulate import SimulatedLearner, run_experiment, run_session

LABELS = {
    "boundary_condition": "边界条件错误",
    "off_by_one_plus": "差一错误（偏大）",
    "arithmetic_operator": "算术运算符误用",
}

PROBLEM_SKILLS = {
    "P1": {"boundary_condition"},                       # 只考一项
    "P2": {"boundary_condition", "off_by_one_plus"},     # 考两项
    "P3": {"arithmetic_operator"},
    "P4": {"off_by_one_plus"},
    "P5": {"boundary_condition", "arithmetic_operator", "off_by_one_plus"},
}


def _attempt(problem_id: str, fault: str = "", skills=None) -> Attempt:
    return Attempt(
        problem_id=problem_id,
        verdict="WA" if fault else "AC",
        fault_operator=fault,
        skills=sorted(skills or PROBLEM_SKILLS.get(problem_id, set())),
    )


# --------------------------------------------------------------------------- #
# 掌握度模型
# --------------------------------------------------------------------------- #


def test_prior_is_uniform_before_any_attempt():
    state = MasteryModel(LABELS).update(LearnerProfile("u"))
    assert len(state) == len(LABELS), "未练过的技能也要在状态里"
    for item in state.values():
        assert item.mean == 0.5
        assert item.opportunities == 0
        assert item.std > 0.2, "没有证据时不确定度应该很大"


def test_errors_lower_mastery_and_evidence_shrinks_uncertainty():
    model = MasteryModel(LABELS)
    profile = LearnerProfile("u")
    before = model.update(profile)["boundary_condition"].std
    for _ in range(6):
        profile.add(_attempt("P1", fault="boundary_condition"))
    state = model.update(profile)["boundary_condition"]
    assert state.mean < 0.3
    assert state.errors == 6
    assert state.std < before, "证据变多，不确定度必须下降"


def test_successes_raise_mastery():
    model = MasteryModel(LABELS)
    profile = LearnerProfile("u")
    for _ in range(6):
        profile.add(_attempt("P1"))
    assert model.update(profile)["boundary_condition"].mean > 0.7


def test_recommendation_prefers_unexplored_skill_but_conclusion_ignores_it():
    """选下一题用下界（会主动补测），下结论用均值（只看证据充分的）——两者必须分开。"""
    model = MasteryModel(LABELS)
    profile = LearnerProfile("u")

    # 情况一：某技能刚犯过错 → 它确实比「还不知道」的技能更值得先练
    profile.add(_attempt("P3", fault="arithmetic_operator"))
    assert model.weakest(profile, limit=1)[0].skill == "arithmetic_operator"
    assert model.weakest_with_evidence(profile, min_opportunities=1).skill == "arithmetic_operator"
    assert model.weakest_with_evidence(profile, min_opportunities=2) is None, "证据不足不下结论"

    # 情况二：某技能做对了 → 该轮到「还没练过」的技能去补测
    passed = LearnerProfile("u2")
    passed.add(_attempt("P3"))  # 做对，没犯错
    top = model.weakest(passed, limit=1)[0]
    assert top.opportunities == 0, "做对之后应当去补测没练过的技能"
    assert top.skill != "arithmetic_operator"


def test_skill_level_labels():
    model = MasteryModel(LABELS)
    profile = LearnerProfile("u")
    for _ in range(8):
        profile.add(_attempt("P1"))
    assert model.update(profile)["boundary_condition"].level in {"基本掌握", "熟练"}


# --------------------------------------------------------------------------- #
# 推荐
# --------------------------------------------------------------------------- #


def test_recommendation_targets_the_target_skill():
    recommender = Recommender(PROBLEM_SKILLS, LABELS)
    recs = recommender.recommend(LearnerProfile("u"), limit=3)
    assert recs
    for rec in recs:
        assert rec.target_skill in PROBLEM_SKILLS[rec.problem_id]


def test_recommendation_avoids_repeating_a_solved_problem():
    recommender = Recommender(PROBLEM_SKILLS, LABELS)
    profile = LearnerProfile("u")
    profile.add(Attempt(problem_id="P1", verdict="AC", skills=["boundary_condition"]))
    recs = recommender.recommend(profile, limit=1)
    assert recs[0].problem_id != "P1"
    assert not recs[0].repeat


def test_recommendation_falls_back_to_repeat_when_all_tried():
    recommender = Recommender({"P1": {"boundary_condition"}}, LABELS)
    profile = LearnerProfile("u")
    profile.add(Attempt(problem_id="P1", verdict="WA", skills=["boundary_condition"]))
    recs = recommender.recommend(profile, limit=1)
    assert recs and recs[0].repeat


def test_explore_strategy_prefers_broader_problems_and_focus_prefers_narrower():
    explore = Recommender(PROBLEM_SKILLS, LABELS, strategy="explore")
    focus = Recommender(PROBLEM_SKILLS, LABELS, strategy="focus")
    profile = LearnerProfile("u")
    pick_explore = explore.recommend(profile, limit=1)[0]
    pick_focus = focus.recommend(profile, limit=1)[0]
    assert len(PROBLEM_SKILLS[pick_explore.problem_id]) >= len(
        PROBLEM_SKILLS[pick_focus.problem_id]
    )


# --------------------------------------------------------------------------- #
# 档案持久化
# --------------------------------------------------------------------------- #


def test_profile_roundtrip(tmp_path: Path):
    profile = LearnerProfile("u1")
    profile.add(_attempt("P1", fault="boundary_condition"))
    path = profile.save(tmp_path / "u1.json")
    loaded = LearnerProfile.load(path)
    assert loaded.user_id == "u1"
    assert loaded.attempts[0].fault_operator == "boundary_condition"
    assert loaded.attempts[0].skills == ["boundary_condition"]


# --------------------------------------------------------------------------- #
# 模拟与对照实验
# --------------------------------------------------------------------------- #


def test_simulated_learner_is_deterministic():
    a = SimulatedLearner("x", {"boundary_condition": 0.5}, seed=7)
    b = SimulatedLearner("x", {"boundary_condition": 0.5}, seed=7)
    assert [a.attempt("P1", {"boundary_condition"}) for _ in range(5)] == [
        b.attempt("P1", {"boundary_condition"}) for _ in range(5)
    ]


def test_practice_reduces_the_true_error_probability():
    learner = SimulatedLearner("x", {"boundary_condition": 0.8}, learning_rate=0.25, seed=1)
    learner.attempt("P1", {"boundary_condition"})
    assert learner.error_prob["boundary_condition"] < 0.8


def test_session_eventually_finds_the_weak_skill():
    labels = dict(LABELS)
    error_prob = {s: 0.05 for s in labels}
    error_prob["boundary_condition"] = 0.8
    learner = SimulatedLearner("u", error_prob, seed=3)
    result, trace = run_session(
        learner, PROBLEM_SKILLS, labels, condition="targeted", budget=40, seed=3
    )
    assert trace, "应当产生了练习轨迹"
    assert result.identified_weakest == "boundary_condition"
    assert result.identified_correctly


def test_experiment_reports_all_three_conditions():
    report = run_experiment(PROBLEM_SKILLS, LABELS, learners=6, budget=25, seed=0)
    for key in ("auto", "focus", "random"):
        assert key in report
        assert report[key]["learners"] == 6
    assert report["config"]["note"].startswith("模拟实验")
