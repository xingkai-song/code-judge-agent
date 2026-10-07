"""公开数据集的下载与格式转换。

把 HumanEval 与 MBPP 转成本项目的题目格式，并单独抽出参考解。

两个数据集的形态差别很大，转换逻辑也因此不同：

* **HumanEval**：``test`` 字段里是一个 ``def check(candidate):`` 函数，断言都在
  函数体里，提交要实现的函数名由 ``entry_point`` 给出。
* **MBPP**：``test_list`` 直接是一组 ``assert`` 字符串，断言里按真实函数名调用；
  ``test_imports`` 提供断言需要的导入。

HumanEval 的断言用 ``ast`` 切分而不是按行切——断言经常跨多行，
按行切会把一条断言拆成两半。``ast.unparse`` 还能顺手把缩进归一化掉。
"""

from __future__ import annotations

import ast
import gzip
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from code_judge.references import ReferenceSolution
from code_judge.types import Problem, ProblemKind, TestCase

USER_AGENT = "code-judge-agent/0.1 (local research use)"


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    display: str
    url: str
    filename: str
    license_note: str


DATASETS: dict[str, DatasetSpec] = {
    "humaneval": DatasetSpec(
        key="humaneval",
        display="HumanEval",
        url="https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz",
        filename="HumanEval.jsonl.gz",
        license_note="MIT (openai/human-eval)",
    ),
    "mbpp": DatasetSpec(
        key="mbpp",
        display="MBPP (sanitized)",
        url=(
            "https://raw.githubusercontent.com/google-research/google-research/"
            "master/mbpp/sanitized-mbpp.json"
        ),
        filename="sanitized-mbpp.json",
        license_note="CC-BY-4.0 (google-research/mbpp)",
    ),
}


@dataclass
class ConversionResult:
    problems: list[Problem]
    references: list[ReferenceSolution]
    skipped: list[tuple[str, str]]


def download(spec: DatasetSpec, raw_dir: Path) -> Path:
    """下载原始数据；已存在就直接复用，保证离线可重跑。"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / spec.filename
    if target.exists() and target.stat().st_size > 0:
        return target
    request = urllib.request.Request(spec.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    target.write_bytes(payload)
    return target


def convert(spec: DatasetSpec, raw_path: Path) -> ConversionResult:
    if spec.key == "humaneval":
        return _convert_humaneval(raw_path)
    if spec.key == "mbpp":
        return _convert_mbpp(raw_path)
    raise KeyError(f"未知数据集：{spec.key}")


# --------------------------------------------------------------------------- #
# HumanEval
# --------------------------------------------------------------------------- #


def _convert_humaneval(raw_path: Path) -> ConversionResult:
    problems: list[Problem] = []
    references: list[ReferenceSolution] = []
    skipped: list[tuple[str, str]] = []

    with gzip.open(raw_path, "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]

    for row in rows:
        task_id = row["task_id"]
        pid = "HumanEval_" + task_id.split("/")[-1]
        entry = row["entry_point"]
        try:
            statements = _humaneval_check_statements(row["test"])
        except SyntaxError as exc:
            skipped.append((pid, f"test 字段无法解析：{exc}"))
            continue
        if not statements:
            skipped.append((pid, "test 字段里没有可用的断言"))
            continue

        cases = [
            TestCase(
                id=f"c{i}",
                assert_code=prefix + stmt,
                note="",
            )
            for i, (prefix, stmt) in enumerate(statements, 1)
        ]
        problems.append(
            Problem(
                id=pid,
                title=entry,
                statement=row["prompt"],
                kind=ProblemKind.ASSERT,
                entry_point=entry,
                time_limit_ms=4000,
                memory_limit_mb=512,
                tags=["humaneval"],
                difficulty="easy",
                source="HumanEval",
                metadata={"task_id": task_id, "license": DATASETS["humaneval"].license_note},
                cases=cases,
            )
        )
        references.append(
            ReferenceSolution(
                id=pid,
                source=row["prompt"] + row["canonical_solution"],
                entry_point=entry,
            )
        )

    return ConversionResult(problems, references, skipped)


def _humaneval_check_statements(test_src: str) -> list[tuple[str, str]]:
    """把 ``def check(candidate)`` 的函数体拆成 (前置导入, 单条语句) 列表。

    导入语句不单独算一条用例，而是作为前缀拼到后续每条用例上——否则它们
    会变成永远通过的「假用例」，把用例总数和通过率算歪。
    """
    tree = ast.parse(test_src)
    body: list[ast.stmt] | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "check":
            body = node.body
            break
    if body is None:
        body = [n for n in tree.body if isinstance(n, ast.Assert)]

    out: list[tuple[str, str]] = []
    prefix = ""
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            prefix += ast.unparse(node) + "\n"
            continue
        out.append((prefix, ast.unparse(node)))
    return out


# --------------------------------------------------------------------------- #
# MBPP
# --------------------------------------------------------------------------- #

_DEF_RE = re.compile(r"^def\s+(\w+)\s*\(", re.MULTILINE)


def _convert_mbpp(raw_path: Path) -> ConversionResult:
    problems: list[Problem] = []
    references: list[ReferenceSolution] = []
    skipped: list[tuple[str, str]] = []
    rows = json.loads(raw_path.read_text(encoding="utf-8"))

    for row in rows:
        pid = f"MBPP_{row['task_id']}"
        code = row.get("code", "")
        match = _DEF_RE.search(code)
        if not match:
            skipped.append((pid, "无法从 code 中解析出函数名"))
            continue
        entry = match.group(1)

        asserts = list(row.get("test_list") or [])
        if not asserts:
            skipped.append((pid, "没有测试断言"))
            continue

        # 每条用例都单独起进程，所以测试导入必须逐条拼上，不能只放在第一条
        prefix = "".join(line + "\n" for line in (row.get("test_imports") or []))
        cases = [
            TestCase(id=f"c{i}", assert_code=prefix + stmt)
            for i, stmt in enumerate(asserts, 1)
        ]
        problems.append(
            Problem(
                id=pid,
                title=entry,
                statement=row.get("prompt", ""),
                kind=ProblemKind.ASSERT,
                entry_point=entry,
                time_limit_ms=4000,
                memory_limit_mb=512,
                tags=["mbpp"],
                difficulty="easy",
                source="MBPP",
                metadata={"task_id": row["task_id"], "license": DATASETS["mbpp"].license_note},
                cases=cases,
            )
        )
        references.append(ReferenceSolution(id=pid, source=code, entry_point=entry))

    return ConversionResult(problems, references, skipped)
