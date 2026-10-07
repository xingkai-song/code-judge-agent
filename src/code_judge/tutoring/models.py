"""学习者档案与推荐结果的数据模型。"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Attempt:
    """一次作答记录。

    ``fault_operator`` 来自诊断闭环——只有在诊断给出被验证的结论时才有值。
    这一条把「判题」和「掌握度建模」接了起来：不是只知道对错，
    而是知道**错在哪一类技能上**。
    """

    problem_id: str
    verdict: str
    passed: int = 0
    total: int = 0
    fault_operator: str = ""
    fault_label: str = ""
    skills: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    ts: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    @property
    def solved(self) -> bool:
        return self.verdict == "AC"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Attempt":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Recommendation:
    """一条推荐：练哪道题、针对哪项技能、为什么。"""

    problem_id: str
    target_skill: str
    target_label: str
    reason: str
    mastery_mean: float
    mastery_lower: float
    mastery_std: float
    candidate_count: int = 0
    repeat: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class LearnerProfile:
    """一个学习者的作答历史。"""

    def __init__(self, user_id: str, attempts: list[Attempt] | None = None) -> None:
        self.user_id = user_id
        self.attempts: list[Attempt] = list(attempts or [])

    def add(self, attempt: Attempt) -> None:
        self.attempts.append(attempt)

    @property
    def attempted_problem_ids(self) -> set[str]:
        return {a.problem_id for a in self.attempts}

    def error_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for a in self.attempts:
            if a.fault_operator:
                counts[a.fault_operator] = counts.get(a.fault_operator, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {"user_id": self.user_id, "attempts": [a.to_dict() for a in self.attempts]}

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "LearnerProfile":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            user_id=data["user_id"],
            attempts=[Attempt.from_dict(a) for a in data.get("attempts", [])],
        )
