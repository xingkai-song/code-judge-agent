"""学习会话：把判题、诊断、掌握度、出题串成一条线。

这一层刻意做成**不依赖终端交互**的普通类，交互只是它外面的一层壳。
好处是整条链路可以被单测覆盖——交互式工具最难测，把逻辑抽出来就好测了。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from code_judge.diagnosis.diagnoser import Diagnoser
from code_judge.diagnosis.explainer import explain
from code_judge.diagnosis.models import Diagnosis
from code_judge.tutoring.mastery import MasteryModel, SkillMastery
from code_judge.tutoring.models import Attempt, LearnerProfile, Recommendation
from code_judge.tutoring.recommender import Recommender
from code_judge.types import Problem, ProblemKind, Verdict
from code_judge.verifier import CodeVerifier


@dataclass
class SubmissionOutcome:
    """一次提交的完整结果。"""

    problem_id: str
    verdict: str
    passed: int
    total: int
    diagnosis: Diagnosis | None = None
    explanation: str = ""
    attempt: Attempt | None = None
    next_recommendations: list[Recommendation] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.verdict == Verdict.AC.value


class TutorSession:
    """一个用户的陪练会话。"""

    def __init__(
        self,
        user_id: str,
        problems: dict[str, Problem],
        problem_skills: dict[str, set[str]],
        labels: dict[str, str],
        verifier: CodeVerifier | None = None,
        diagnoser: Diagnoser | None = None,
        profile_path: str | Path | None = None,
    ) -> None:
        self.user_id = user_id
        self.problems = problems
        self.problem_skills = problem_skills
        self.labels = labels
        self.verifier = verifier or CodeVerifier()
        self.diagnoser = diagnoser or Diagnoser(verifier=self.verifier)
        self.profile_path = Path(profile_path) if profile_path else None

        self.profile = (
            LearnerProfile.load(self.profile_path)
            if self.profile_path and self.profile_path.exists()
            else LearnerProfile(user_id)
        )
        self.mastery = MasteryModel(labels)
        self.recommender = Recommender(problem_skills, labels, self.mastery, self._attempt_counts())

    # -- 出题 ----------------------------------------------------------------

    def recommend(self, limit: int = 3) -> list[Recommendation]:
        return self.recommender.recommend(self.profile, limit=limit)

    def mastery_snapshot(self) -> list[SkillMastery]:
        return self.mastery.sorted_skills(self.profile)

    def weakest_with_evidence(self, min_opportunities: int = 2) -> SkillMastery | None:
        return self.mastery.weakest_with_evidence(self.profile, min_opportunities)

    def problem(self, problem_id: str) -> Problem | None:
        return self.problems.get(problem_id)

    # -- 做题 ----------------------------------------------------------------

    def starter_code(self, problem: Problem) -> str:
        """给出一份起步模板，让用户不必凭空猜函数签名。"""
        if problem.kind is ProblemKind.ASSERT:
            # HumanEval 的题面本身就是「函数签名 + docstring」，直接当模板最省事
            return problem.statement.rstrip() + "\n    pass\n"
        return ""

    def submit(
        self, source: str, problem_id: str, language: str = "python"
    ) -> SubmissionOutcome:
        """提交一份代码：判题 → （失败则）诊断 → 记录 → 给出下一题。"""
        problem = self.problems.get(problem_id)
        if problem is None:
            raise KeyError(f"题库里没有 {problem_id!r}")

        started = time.monotonic()
        report = self.verifier.verify(source, problem, language)

        diagnosis: Diagnosis | None = None
        text = ""
        fault_operator = ""
        fault_label = ""

        if report.verdict is Verdict.AC:
            text = "通过。全部测试用例都过了。"
        else:
            # 只在失败时才跑诊断——诊断要枚举修复候选、逐个实跑，是有成本的
            diagnosis = self.diagnoser.diagnose(source, problem, language)
            text = explain(diagnosis)
            if diagnosis.verified:
                fault_operator = diagnosis.operator
                fault_label = diagnosis.fault_label

        attempt = Attempt(
            problem_id=problem_id,
            verdict=report.verdict.value,
            passed=report.passed_count,
            total=report.total_count,
            fault_operator=fault_operator,
            fault_label=fault_label,
            skills=sorted(self.problem_skills.get(problem_id, set())),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        self.profile.add(attempt)
        self._save()

        return SubmissionOutcome(
            problem_id=problem_id,
            verdict=report.verdict.value,
            passed=report.passed_count,
            total=report.total_count,
            diagnosis=diagnosis,
            explanation=text,
            attempt=attempt,
            next_recommendations=self.recommend(limit=2),
        )

    # -- 内部 ----------------------------------------------------------------

    def _attempt_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for attempt in self.profile.attempts:
            counts[attempt.problem_id] = counts.get(attempt.problem_id, 0) + 1
        return counts

    def _save(self) -> None:
        if self.profile_path:
            self.profile.save(self.profile_path)
        # 作答改变了「哪些题被练过」，推荐器的计数要跟着更新
        self.recommender.attempt_counts = self._attempt_counts()

    # -- 展示 ----------------------------------------------------------------

    def profile_report(self) -> str:
        lines = [f"学习者：{self.user_id}　累计练习 {len(self.profile.attempts)} 次"]
        solved = sum(1 for a in self.profile.attempts if a.solved)
        lines.append(f"一次通过 {solved} 次，未通过 {len(self.profile.attempts) - solved} 次")
        errors = self.profile.error_counts()
        if errors:
            top = sorted(errors.items(), key=lambda kv: -kv[1])[:3]
            desc = "、".join(f"{self.labels.get(k, k)}×{v}" for k, v in top)
            lines.append(f"最常犯的错因：{desc}")

        lines.append("")
        lines.append(f"{'技能':<18}{'掌握度':>8}{'不确定度':>10}{'练过':>6}{'犯错':>6}  评价")
        lines.append("-" * 68)
        for item in self.mastery_snapshot():
            if item.opportunities == 0:
                continue
            lines.append(
                f"{item.label:<18}{item.mean:>7.0%}{item.std:>10.0%}"
                f"{item.opportunities:>6}{item.errors:>6}  {item.level}"
            )
        unseen = [m for m in self.mastery_snapshot() if m.opportunities == 0]
        if unseen:
            lines.append("")
            lines.append(f"还没练过的技能（{len(unseen)} 项）："
                         + "、".join(m.label for m in unseen))
        weakest = self.weakest_with_evidence()
        if weakest:
            lines.append("")
            lines.append(f"当前最需要加强：{weakest.label}"
                         f"（掌握度 {weakest.mean:.0%}，练过 {weakest.opportunities} 次）")
        return "\n".join(lines)
