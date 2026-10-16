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
from code_judge.types import Checker, Problem, ProblemKind, TestCase

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


# --------------------------------------------------------------------------- #
# CodeContests
# --------------------------------------------------------------------------- #

#: CodeContests 的 language 枚举（见 contest_problem.proto）
_CC_PYTHON3 = 3

_CC_SOURCE_NAMES = {
    1: "CodeChef",
    2: "Codeforces",
    3: "HackerEarth",
    4: "Code Jam",
    6: "AtCoder",
    7: "Aizu",
}

_CC_DIFFICULTY_NAMES = {
    1: "easy",
    2: "medium",
    3: "hard",
    4: "harder",
    5: "hardest",
    6: "external",
}

#: 单条用例的输入/输出上限。CodeContests 里有题的用例大到几 MB，
#: 全收下来会让判题和存储都失控（每条用例都要单独起一次进程）。
_CC_MAX_CASE_BYTES = 64 * 1024

_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class CcEntry:
    """一道 CodeContests 题 + 它全部可用的 Python3 参考解。"""

    problem: Problem
    candidates: list[str]

    @property
    def id(self) -> str:
        return self.problem.id


def convert_codecontests_entries(
    rows: Iterable[dict],
    max_cases: int = 8,
    max_case_bytes: int = _CC_MAX_CASE_BYTES,
) -> tuple[list[CcEntry], list[tuple[str, str]]]:
    """把 CodeContests 的行转成本项目的题目格式（标准输入输出型）。

    三条刻意的取舍，都写在代码里：

    * **只收有 Python3 参考解的题**。诊断闭环目前只支持 Python，没有参考解
      就产不出错因标签，也做不了变异测试。
    * **每题的用例数设上限**。这是完整程序题：每条用例都要单独起一次进程，
      一道题挂 200 条用例会让一次诊断从几秒涨到几分钟。
    * **公开用例优先**。``public_tests`` 是题面里就给的样例，学生能自己看到；
      不够再用 ``generated_tests`` 补——这些是自动生成并用正确解验证过的，
      比 private_tests 更适合教学场景。

    这里保留**每道题的全部候选解**（而不是只取第一份），是因为 CodeContests
    是研究数据集，里面混着错解、坏用例和 Python2 残留代码：到底哪份解真的能过，
    要让判题机跑一遍才知道。筛选在导入脚本里做。
    """
    entries: list[CcEntry] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()

    for index, row in enumerate(rows, 1):
        pid = _codecontests_id(row, index)
        if pid in seen:
            continue
        seen.add(pid)

        candidates = _cc_python_solutions(row)
        if not candidates:
            skipped.append((pid, "没有 Python3 参考解"))
            continue

        cases = _cc_pick_cases(row, max_cases, max_case_bytes)
        if not cases:
            skipped.append((pid, "没有可用的测试用例"))
            continue

        time_ms, memory_mb = _cc_limits(row)
        tags = [str(t) for t in (row.get("cf_tags") or [])]
        rating = row.get("cf_rating") or 0
        source_key = int(row.get("source") or 0)

        entries.append(
            CcEntry(
                problem=Problem(
                    id=pid,
                    title=str(row.get("name") or pid),
                    statement=_clean_statement(row.get("description") or ""),
                    kind=ProblemKind.STDIO,
                    time_limit_ms=time_ms,
                    memory_limit_mb=memory_mb,
                    checker=Checker.TOKEN,
                    tags=tags,
                    difficulty=str(rating)
                    if rating
                    else _CC_DIFFICULTY_NAMES.get(int(row.get("difficulty") or 0), ""),
                    source="CodeContests",
                    metadata={
                        "contests_source": _CC_SOURCE_NAMES.get(source_key, "unknown"),
                        "cf_rating": rating,
                        "cf_tags": tags,
                        "cf_contest_id": row.get("cf_contest_id"),
                        "cf_index": row.get("cf_index"),
                        "license": "Apache-2.0 (deepmind/code_contests)",
                    },
                    cases=cases,
                ),
                candidates=candidates,
            )
        )

    return entries, skipped


def convert_codecontests(
    rows: Iterable[dict],
    max_cases: int = 8,
    max_case_bytes: int = _CC_MAX_CASE_BYTES,
) -> ConversionResult:
    """便捷入口：不跑筛选，直接取每道题的第一份参考解。"""
    entries, skipped = convert_codecontests_entries(rows, max_cases, max_case_bytes)
    problems = [e.problem for e in entries]
    references = [
        ReferenceSolution(id=e.id, source=e.candidates[0], entry_point="") for e in entries
    ]
    return ConversionResult(problems, references, skipped)


def _codecontests_id(row: dict, fallback_index: int) -> str:
    """生成稳定的题目 id。

    优先用 Codeforces 的比赛号 + 题号（``CC_1575_A``），可读且稳定；
    没有编号的题库（AtCoder / CodeChef / Aizu 等）退化成序号。
    """
    contest = row.get("cf_contest_id")
    index = str(row.get("cf_index") or "").strip()
    if contest and index:
        return f"CC_{contest}_{index}"
    return f"CC_{fallback_index:05d}"


def _cc_python_solutions(row: dict) -> list[str]:
    """取全部 Python3 参考解（去重后保序）。"""
    solutions = row.get("solutions") or {}
    languages = solutions.get("language") or []
    codes = solutions.get("solution") or []
    out: list[str] = []
    for language, code in zip(languages, codes):
        if int(language or 0) == _CC_PYTHON3 and code:
            text = str(code)
            if text not in out:
                out.append(text)
    return out


def _cc_pick_cases(
    row: dict, max_cases: int, max_case_bytes: int
) -> list[TestCase]:
    """按「公开 → 生成」的顺序凑够用例，跳过过大的那条。"""
    ordered = [
        ("public", row.get("public_tests")),
        ("private", row.get("private_tests")),
        ("generated", row.get("generated_tests")),
    ]
    cases: list[TestCase] = []
    for label, group in ordered:
        if not group or len(cases) >= max_cases:
            continue
        inputs = group.get("input") or []
        outputs = group.get("output") or []
        for stdin, expected in zip(inputs, outputs):
            if len(cases) >= max_cases:
                break
            if stdin is None or expected is None:
                continue
            # 用例太大就跳过：判题时会真的把它喂给程序，太大既慢又没意义
            if len(stdin) > max_case_bytes or len(expected) > max_case_bytes:
                continue
            cases.append(
                TestCase(
                    id=f"c{len(cases) + 1}",
                    stdin=str(stdin),
                    expected_stdout=str(expected),
                    note=label,
                )
            )
    return cases


def _cc_limits(row: dict) -> tuple[int, int]:
    """把 CodeContests 的时间和内存限制换算成本项目的单位。

    时间放宽 2 倍：Codeforces 的限制是给 C++ 定的，Python 解释器本身就要慢
    一个量级，不作调整的话参考解会大面积「超时」。
    """
    limit = row.get("time_limit") or {}
    seconds = int(limit.get("seconds") or 0)
    nanos = int(limit.get("nanos") or 0)
    time_ms = int(seconds * 1000 + nanos / 1_000_000)
    if time_ms <= 0:
        time_ms = 2000
    time_ms = min(time_ms * 2, 20_000)

    memory_mb = int((row.get("memory_limit_bytes") or 0) // (1024 * 1024))
    if memory_mb <= 0:
        memory_mb = 256
    return time_ms, min(memory_mb, 1024)


def _clean_statement(text: str) -> str:
    """题面里混着 HTML 标签和转义实体，清掉再存，前端直接当纯文本显示。"""
    if not text:
        return ""
    import html as _html

    cleaned = _HTML_TAG_RE.sub("", str(text))
    cleaned = _html.unescape(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
