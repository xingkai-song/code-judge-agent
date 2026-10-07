"""模拟学员与对照实验。

真实用户还没到位之前，用模拟学员先把机制验证一遍：能不能**认出**学生的弱项，
以及靶向练习比随机练习**省多少**。

模拟学员的假设必须写清楚，否则结论会被误读：

* 每项技能有一个真实错误概率 p；做题时按 p 抽样决定是否犯这类错误。
* 练一次该技能，p 乘上 (1 - 学习率)，也就是**边际收益递减**。
* 每次练习都会让该题涉及的所有技能各降一点。

**这是模拟，不是真人实验。** 它能说明「机制按预期工作」，不能说明
「对真实学生有多大效果」——后者需要真实用户数据。这一点在报告里也要写出来。
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field

from code_judge.tutoring.mastery import MasteryModel
from code_judge.tutoring.models import Attempt, LearnerProfile
from code_judge.tutoring.recommender import Recommender


@dataclass
class SimulatedLearner:
    """一个带有已知弱项的模拟学生。"""

    user_id: str
    error_prob: dict[str, float]
    learning_rate: float = 0.25
    floor: float = 0.02
    seed: int = 0
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    @property
    def weakest_skill(self) -> str:
        return max(self.error_prob, key=lambda k: self.error_prob[k])

    def attempt(self, problem_id: str, skills: set[str]) -> tuple[str, str]:
        """做一道题，返回 (判定, 犯错的技能)。"""
        fault = ""
        for skill in sorted(skills):
            if self._rng.random() < self.error_prob.get(skill, 0.0):
                fault = skill
                break
        # 做过就学一点，边际收益递减
        for skill in skills:
            self.error_prob[skill] = max(
                self.floor, self.error_prob.get(skill, 0.0) * (1 - self.learning_rate)
            )
        return ("WA" if fault else "AC"), fault


@dataclass
class SimulationResult:
    user_id: str
    condition: str
    attempts: int
    attempts_to_mastery: int | None
    final_true_error_prob: float
    identified_weakest: str
    identified_correctly: bool
    target_hit_rate: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def run_session(
    learner: SimulatedLearner,
    problem_skills: dict[str, set[str]],
    labels: dict[str, str],
    *,
    condition: str = "targeted",
    budget: int = 60,
    mastery_threshold: float = 0.2,
    seed: int = 0,
) -> tuple[SimulationResult, list[dict]]:
    """跑一个学习会话。

    condition 取 ``auto``（先广后窄，默认）、``focus``（只练聚焦题）、
    ``random``（随机出题，对照组）。
    """
    rng = random.Random(f"{seed}:{condition}")
    profile = LearnerProfile(user_id=learner.user_id)
    model = MasteryModel(labels)
    strategy = "auto" if condition == "targeted" else condition
    recommender = Recommender(problem_skills, labels, model, strategy=strategy)
    problem_ids = sorted(problem_skills)

    targets_hit = 0
    trace: list[dict] = []
    attempts_to_mastery: int | None = None
    true_weak = learner.weakest_skill

    for step in range(1, budget + 1):
        if condition in ("targeted", "auto", "focus"):
            picks = recommender.recommend(profile, limit=1)
            if picks:
                problem_id = picks[0].problem_id
                hit = true_weak in problem_skills[problem_id]
                reason = picks[0].reason
            else:
                problem_id = rng.choice(problem_ids)
                hit = true_weak in problem_skills[problem_id]
                reason = "没有可推荐的题，随机选一道"
        else:
            problem_id = rng.choice(problem_ids)
            hit = true_weak in problem_skills[problem_id]
            reason = "随机出题（对照组）"

        targets_hit += int(hit)
        skills = problem_skills[problem_id]
        verdict, fault = learner.attempt(problem_id, skills)
        profile.add(
            Attempt(
                problem_id=problem_id,
                verdict=verdict,
                passed=0 if fault else 1,
                total=1,
                fault_operator=fault,
                fault_label=labels.get(fault, "") if fault else "",
                skills=sorted(skills),
            )
        )
        trace.append(
            {
                "step": step,
                "problem_id": problem_id,
                "target_hit": hit,
                "verdict": verdict,
                "fault": fault,
                "reason": reason,
            }
        )

        if attempts_to_mastery is None and learner.error_prob[true_weak] < mastery_threshold:
            attempts_to_mastery = step
            break

    # 下结论时用「证据充分的最弱技能」，而不是选下一题用的下界排序
    conclusion = model.weakest_with_evidence(profile, min_opportunities=2)
    identified = conclusion.skill if conclusion else ""
    return (
        SimulationResult(
            user_id=learner.user_id,
            condition=condition,
            attempts=len(profile.attempts),
            attempts_to_mastery=attempts_to_mastery,
            final_true_error_prob=round(learner.error_prob[true_weak], 3),
            identified_weakest=identified,
            identified_correctly=identified == true_weak,
            target_hit_rate=round(targets_hit / max(1, len(profile.attempts)), 3),
        ),
        trace,
    )


def run_experiment(
    problem_skills: dict[str, set[str]],
    labels: dict[str, str],
    learners: int = 30,
    budget: int = 60,
    identify_after: int = 30,
    seed: int = 0,
) -> dict:
    """对照实验：先广后窄（auto） vs 只练聚焦题（focus） vs 随机出题。"""
    skills = sorted(labels)
    rows: list[dict] = []
    for i in range(learners):
        rng = random.Random(f"{seed}:learner:{i}")
        weak = rng.choice(skills)
        # 弱项错误率高，其余技能低
        error_prob = {s: rng.uniform(0.05, 0.15) for s in skills}
        error_prob[weak] = rng.uniform(0.6, 0.85)

        for condition in ("targeted", "focus", "random"):
            learner = SimulatedLearner(
                user_id=f"L{i}-{condition}",
                error_prob=dict(error_prob),
                seed=hash((seed, i, condition)) % (2**31),
            )
            result, trace = run_session(
                learner, problem_skills, labels,
                condition=condition, budget=budget, seed=seed,
            )
            row = result.to_dict()
            row["true_weak_skill"] = weak
            # 「第几步才认对弱项」直接在这条轨迹上回放得到，不重新跑一遍
            row["identified_at"] = _first_correct_identification(
                trace, problem_skills, labels, weak, identify_after
            )
            rows.append(row)

    def summarise(group: list[dict]) -> dict:
        reached = [r["attempts_to_mastery"] for r in group if r["attempts_to_mastery"]]
        identified = [r["identified_at"] for r in group if r["identified_at"]]
        return {
            "learners": len(group),
            "mastery_reached_rate": round(len(reached) / max(1, len(group)), 4),
            "avg_attempts_to_mastery": round(statistics.mean(reached), 2) if reached else None,
            "avg_attempts": round(statistics.mean(r["attempts"] for r in group), 2),
            "weak_skill_hit_rate": round(statistics.mean(r["target_hit_rate"] for r in group), 4),
            "identify_accuracy": round(
                sum(1 for r in group if r["identified_correctly"]) / max(1, len(group)), 4
            ),
            "avg_attempts_to_identify": round(statistics.mean(identified), 2) if identified else None,
        }

    return {
        "config": {
            "learners": learners,
            "budget": budget,
            "identify_after": identify_after,
            "seed": seed,
            "note": "模拟实验：学员按固定错误概率抽样、练习后边际递减，不是真人实验",
        },
        "auto": summarise([r for r in rows if r["condition"] == "targeted"]),
        "focus": summarise([r for r in rows if r["condition"] == "focus"]),
        "random": summarise([r for r in rows if r["condition"] == "random"]),
        "rows": rows,
    }


def _first_correct_identification(
    trace: list[dict],
    problem_skills: dict[str, set[str]],
    labels: dict[str, str],
    true_weak: str,
    limit: int,
) -> int | None:
    """在这条轨迹上前缀回放，看第几步时掌握度模型的判断已经和真相一致。"""
    profile = LearnerProfile(user_id="replay")
    model = MasteryModel(labels)
    for item in trace[:limit]:
        profile.add(
            Attempt(
                problem_id=item["problem_id"],
                verdict=item["verdict"],
                fault_operator=item["fault"],
                skills=sorted(problem_skills[item["problem_id"]]),
            )
        )
        top = model.weakest_with_evidence(profile, min_opportunities=2)
        if top and top.skill == true_weak:
            return int(item["step"])
    return None
