"""针对性出题。

选题分两步：

1. **选技能**：取掌握度下界最低的那项。用下界而不是均值，让系统对练得少的
   技能保持「不确定就先补测」的态度，而不是一直围着已知弱项转。
2. **选题**：从能考察该技能的题里挑一道。优先级是
   ① 没做过的 > ② 只考这一项技能的（越聚焦越好） > ③ 别人做得少的（更有区分度）。

第二条件「只考这一项技能」很关键：如果一个题目同时考察五种技能，
学生做错了也说不清是哪一项的问题，练习的针对性就被稀释了。
"""

from __future__ import annotations

from dataclasses import dataclass

from code_judge.tutoring.mastery import MasteryModel
from code_judge.tutoring.models import Attempt, LearnerProfile, Recommendation


@dataclass
class _Candidate:
    problem_id: str
    skills: set[str]


class Recommender:
    def __init__(
        self,
        problem_skills: dict[str, set[str]],
        labels: dict[str, str] | None = None,
        mastery: MasteryModel | None = None,
        attempt_counts: dict[str, int] | None = None,
        strategy: str = "auto",
        coverage_target: float = 0.6,
    ) -> None:
        self.problem_skills = problem_skills
        self.labels = dict(labels or {})
        self.mastery = mastery or MasteryModel(self.labels)
        #: 全局作答次数，用来偏向「被练得少」的题目
        self.attempt_counts = dict(attempt_counts or {})
        self.strategy = strategy
        self.coverage_target = coverage_target

    # -- 对外 ---------------------------------------------------------------

    def recommend(self, profile: LearnerProfile, limit: int = 3) -> list[Recommendation]:
        ranked = self.mastery.weakest(profile, limit=len(self.labels) or 11)
        done = profile.attempted_problem_ids
        used_skills: set[str] = set()
        chosen: set[str] = set()
        fresh: list[Recommendation] = []
        repeats: list[Recommendation] = []

        for skill in ranked:
            if skill.skill in used_skills:
                continue
            pick = self._pick_problem(skill.skill, done, profile, exclude=chosen)
            if pick is None:
                continue
            problem_id, repeat, candidates = pick
            recommendation = Recommendation(
                problem_id=problem_id,
                target_skill=skill.skill,
                target_label=skill.label,
                reason=self._reason(profile, skill.skill, problem_id, repeat),
                mastery_mean=round(skill.mean, 3),
                mastery_lower=round(skill.lower_bound(self.mastery.pessimism), 3),
                mastery_std=round(skill.std, 3),
                candidate_count=candidates,
                repeat=repeat,
            )
            # 同一道题可能同时覆盖多项技能：把已经推过的题排除掉，
            # 让下面几个技能换一道题，而不是白白浪费一个推荐位。
            used_skills.add(skill.skill)
            chosen.add(problem_id)
            bucket = repeats if repeat else fresh
            bucket.append(recommendation)

        # 没做过的题优先推荐；只有在新题不够时才回头推做过的（复测）
        return (fresh + repeats)[:limit]

    # -- 内部 ---------------------------------------------------------------

    def _pick_problem(
        self,
        skill: str,
        done: set[str],
        profile: LearnerProfile,
        exclude: set[str] | None = None,
    ) -> tuple[str, bool, int] | None:
        pool = [
            (pid, skills)
            for pid, skills in self.problem_skills.items()
            if skill in skills
        ]
        if exclude:
            pool = [item for item in pool if item[0] not in exclude]
        if not pool:
            return None

        fresh = [item for item in pool if item[0] not in done]
        repeat = not fresh
        candidates = fresh or pool

        # 选「聚焦」还是「覆盖」，取决于当前对全局的了解程度。
        #
        # 实测发现：一味挑「只考一项」的题虽然练习效率高，但早期认弱项反而更慢——
        # 覆盖面太窄，很多技能迟迟攒不到证据。所以先广后窄：
        # 已知技能覆盖率不足时优先选能一次覆盖多项技能的题，把画像铺开，
        # 覆盖够了再收窄，做针对性训练。
        breadth = self._should_explore(profile)
        # ① 未做过 ② 广度/聚焦 ③ 全局练得少 ④ id 稳定排序
        candidates.sort(
            key=lambda item: (
                -len(item[1]) if breadth else len(item[1]),
                self.attempt_counts.get(item[0], 0),
                item[0],
            )
        )
        return candidates[0][0], repeat, len(pool)

    def _should_explore(self, profile: LearnerProfile) -> bool:
        if self.strategy == "explore":
            return True
        if self.strategy == "focus":
            return False
        state = self.mastery.update(profile)
        if not state:
            return True
        covered = sum(1 for m in state.values() if m.opportunities > 0)
        return covered / len(state) < self.coverage_target

    def _reason(
        self, profile: LearnerProfile, skill: str, problem_id: str, repeat: bool
    ) -> str:
        label = self.labels.get(skill, skill)
        state = self.mastery.update(profile).get(skill)
        base = (
            f"你在「{label}」上的掌握度估计是 {state.mean:.0%}"
            f"（不确定度 ±{state.std:.0%}，练过 {state.opportunities} 次）"
            if state
            else f"还不了解你在「{label}」上的情况，先做一道看看"
        )
        extra = "，这道题之前做过，用来复测" if repeat else "，选一道没做过、且只考这一项的题"
        return base + extra
