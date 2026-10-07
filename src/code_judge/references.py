"""参考解（reference solution）的存取。

参考解单独存，不塞进题目定义里——一道题不该自带答案，而参考解真正的用途是
后面变异测试的输入：注入错误、生成错因标签，都要从它出发。

格式同样用 JSONL：``{"id": ..., "source": ..., "entry_point": ...}``
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ReferenceSolution:
    id: str
    source: str
    entry_point: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "source": self.source, "entry_point": self.entry_point}

    @classmethod
    def from_dict(cls, d: dict) -> "ReferenceSolution":
        return cls(id=str(d["id"]), source=d["source"], entry_point=d.get("entry_point", ""))


def save_references(refs: Iterable[ReferenceSolution], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for ref in refs:
            fh.write(json.dumps(ref.to_dict(), ensure_ascii=False) + "\n")
    return target


def load_references(path: str | Path) -> dict[str, ReferenceSolution]:
    refs: dict[str, ReferenceSolution] = {}
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ref = ReferenceSolution.from_dict(json.loads(line))
            except Exception as exc:
                raise ValueError(f"{path}:{lineno} 解析失败：{exc}") from exc
            refs[ref.id] = ref
    return refs
