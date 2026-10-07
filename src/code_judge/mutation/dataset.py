"""带错因标签的变异数据集。

只保留**被判题机判定为失败**的变异体。这条规则是整个数据集可信的基础：
标签不是「我以为这样改会错」，而是「判题机证明这样改确实错了」。

仍然通过的变异体叫**等价变异体**（equivalent mutant）——说明这个改动在当前
测试下不改变行为，或者测试用例覆盖不到那处分支。等价变异率本身就是一个有意义的
指标：它反映这套测试用例对错误的检出能力。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class LabelledMutant:
    id: str
    problem_id: str
    source: str
    operator: str
    error_label: str
    verdict: str
    failed_case: str = ""
    detail: str = ""
    lineno: int = 0
    before: str = ""
    after: str = ""
    cascaded: bool = False
    elapsed_ms: int = 0
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LabelledMutant":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def save_mutants(mutants: Iterable[LabelledMutant], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for mutant in mutants:
            fh.write(json.dumps(mutant.to_dict(), ensure_ascii=False) + "\n")
    return target


def load_mutants(path: str | Path) -> list[LabelledMutant]:
    out: list[LabelledMutant] = []
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(LabelledMutant.from_dict(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"{path}:{lineno} 解析失败：{exc}") from exc
    return out
