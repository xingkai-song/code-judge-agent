"""题目文件的读写。

题目用 JSON 存储，一个文件一道题。这个格式刻意做得简单，目的是让
后面的批量化流程（从公开数据集导入、变异生成）只需要产出同样的 JSON，
判题链路完全不用改。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from code_judge.types import Problem


def load_problem(path: str | Path) -> Problem:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Problem.from_dict(data)


def load_problems(directory: str | Path) -> list[Problem]:
    """按文件名排序加载目录下所有题目。"""
    files = sorted(Path(directory).glob("*.json"))
    return [load_problem(p) for p in files]


def save_problem(problem: Problem, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(problem.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_problems_jsonl(path: str | Path) -> list[Problem]:
    """按 JSONL 加载题库。

    题库规模上千之后，一个题目一个文件会让目录变得难以浏览，
    因此导入的数据集统一用 JSONL：一行一道题。
    """
    problems: list[Problem] = []
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                problems.append(Problem.from_dict(json.loads(line)))
            except Exception as exc:  # 定位到具体是哪一行坏了
                raise ValueError(f"{path}:{lineno} 解析失败：{exc}") from exc
    return problems


def save_problems_jsonl(problems: Iterable[Problem], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for problem in problems:
            fh.write(json.dumps(problem.to_dict(), ensure_ascii=False) + "\n")
    return target


def load_any(path: str | Path) -> list[Problem]:
    """目录按 JSON 加载，``.jsonl`` 文件按 JSONL 加载。"""
    p = Path(path)
    if p.is_dir():
        return load_problems(p)
    if p.suffix == ".jsonl":
        return load_problems_jsonl(p)
    return [load_problem(p)]


def load_library(root: str | Path) -> dict[str, Problem]:
    """加载整个题库：``<root>/samples/*.json`` 加上 ``<root>/*.jsonl``。

    返回 ``{problem_id: Problem}``。示例题和数据集题目合在一起，
    这样上层（诊断、出题、交互工具）只需要拿一个字典就够。
    """
    root = Path(root)
    index: dict[str, Problem] = {}
    samples = root / "samples"
    if samples.is_dir():
        for problem in load_problems(samples):
            index[problem.id] = problem
    for path in sorted(root.glob("*.jsonl")):
        for problem in load_problems_jsonl(path):
            index[problem.id] = problem
    return index
