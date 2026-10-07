"""掌握度估计：Beta-Binomial 模型。

每项技能维护一个 Beta(α, β) 后验：

* 在一次**能考察该技能**的作答里，学生没有犯这类错误 → α += 1
* 犯了这类错误 → β += 1

掌握度取后验均值，不确定度取后验标准差。

为什么用这个模型而不是更复杂的：

1. **自带不确定度**。练过 1 次和练过 30 次，即使均值一样，可信程度完全不同，
   而选题策略正需要这个信息。
2. **可解释**。均值就是「没犯错的次数 / 总次数」的平滑版本，一眼能讲清楚，
   不需要向学生解释模型内部。
3. **没有额外依赖**，几十行就能写对，也不会过拟合——现在的数据量还撑不起
   更复杂的模型。

模型的一个明确假设：**题目考察技能 k，学生在这题上没犯 k 类错误，就算作
k 的一次成功**。这是弱证据（可能是题目没触发到），所以先验取 Beta(1,1) 均匀分布，
而且后验标准差会随着证据增加而下降——证据弱的时候系统本来就该保持不确定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from code_judge.tutoring.models import LearnerProfile


@dataclass
class SkillMastery:
    skill: str
    label: str
    alpha: float
    beta: float
    opportunities: int = 0
    errors: int = 0

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def std(self) -> float:
        total = self.alpha + self.beta
        var = (self.alpha * self.beta) / (total * total * (total + 1))
        return math.sqrt(var)

    def lower_bound(self, pessimism: float = 0.5) -> float:
        """悲观下界：均值减去 k 倍标准差。

        用下界排序等价于「乐观面对不确定性」的反面——不确定就先补测。
        """
        return self.mean - pessimism * self.std

    @property
    def level(self) -> str:
        m = self.mean
        if m >= 0.85:
            return "熟练"
        if m >= 0.65:
            return "基本掌握"
        if m >= 0.4:
            return "不稳"
        return "薄弱"


class MasteryModel:
    """按作答历史计算每项技能的掌握度。"""

    def __init__(
        self,
        labels: dict[str, str] | None = None,
        prior_alpha: float = 1.0,
        prior_beta: float = 1.0,
        pessimism: float = 0.5,
    ) -> None:
        self.labels = dict(labels or {})
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta
        self.pessimism = pessimism

    def update(self, profile: LearnerProfile) -> dict[str, SkillMastery]:
        # 已知技能一开始就全部进入状态，即使一次都没练过。
        #
        # 这个细节很关键：早期版本只把「作答里出现过的技能」加进状态，结果是
        # 系统只能在已经见过的技能里挑，会反复练同一个弱项，**永远发现不了
        # 那些一次都没机会暴露的技能**。未练过的技能应当以先验（均值 0.5、
        # 不确定度最大）参与排序，下界最低，自然被优先补测——这就是探索。
        state: dict[str, SkillMastery] = {
            skill: SkillMastery(
                skill=skill,
                label=label,
                alpha=self.prior_alpha,
                beta=self.prior_beta,
            )
            for skill, label in self.labels.items()
        }

        def slot(skill: str) -> SkillMastery:
            if skill not in state:
                state[skill] = SkillMastery(
                    skill=skill,
                    label=self.labels.get(skill, skill),
                    alpha=self.prior_alpha,
                    beta=self.prior_beta,
                )
            return state[skill]

        for attempt in profile.attempts:
            for skill in attempt.skills:
                item = slot(skill)
                item.opportunities += 1
                if attempt.fault_operator == skill:
                    item.beta += 1
                    item.errors += 1
                else:
                    item.alpha += 1
        return state

    def weakest(
        self, profile: LearnerProfile, limit: int = 3, min_opportunities: int = 0
    ) -> list[SkillMastery]:
        """按悲观下界排序，用于**选下一道题**。

        未练过的技能也会参与（下界最低），所以系统会主动去补测——这是有意的探索。
        """
        state = self.update(profile)
        items = [m for m in state.values() if m.opportunities >= min_opportunities]
        items.sort(key=lambda m: (m.lower_bound(self.pessimism), m.mean))
        return items[:limit]

    def weakest_with_evidence(
        self, profile: LearnerProfile, min_opportunities: int = 2
    ) -> SkillMastery | None:
        """按均值找出**证据充分**的最弱技能，用于「下结论」而不是「选下一题」。

        两个场景的要求正好相反：

        * 选下一题时，没练过的技能必须参与排序（下界最低 → 优先补测），否则系统
          会一直重复练已知的弱项，永远发现不了别的短板。
        * 下结论时，没练过的技能必须排除，否则「最弱技能」永远指向那个
          「还不知道」的技能，结论没有意义。
        """
        state = self.update(profile)
        items = [m for m in state.values() if m.opportunities >= min_opportunities]
        if not items:
            return None
        return min(items, key=lambda m: (m.mean, -m.opportunities))

    def sorted_skills(self, profile: LearnerProfile) -> list[SkillMastery]:
        state = self.update(profile)
        return sorted(state.values(), key=lambda m: m.mean)
