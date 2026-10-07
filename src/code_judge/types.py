"""判题领域模型。

这一层只描述「判题结果长什么样」，不涉及任何执行细节，方便被诊断层、
评测层和后续的变异测试直接复用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    """单次判定结果。"""

    AC = "AC"    # 通过
    WA = "WA"    # 答案错误
    TLE = "TLE"  # 超时
    MLE = "MLE"  # 内存超限
    RE = "RE"    # 运行时错误
    OLE = "OLE"  # 输出超限
    CE = "CE"    # 编译错误
    IE = "IE"    # 判题机自身异常（不是选手的错）

    @property
    def rank(self) -> int:
        """严重程度，用于把多个用例汇总成一个总判定。"""
        return _RANK[self]

    @property
    def is_accepted(self) -> bool:
        return self is Verdict.AC


_RANK: dict[Verdict, int] = {
    Verdict.AC: 0,
    Verdict.WA: 1,
    Verdict.RE: 2,
    Verdict.OLE: 3,
    Verdict.TLE: 4,
    Verdict.MLE: 5,
    Verdict.CE: 6,
    Verdict.IE: 7,
}


class Checker(str, Enum):
    """输出比对方式。"""

    EXACT = "exact"  # 逐字符（先做换行与行尾空白归一化）
    TOKEN = "token"  # 按空白切分后逐 token 比较，容忍多余空格与换行
    FLOAT = "float"  # 按浮点数比较，带容差


class ProblemKind(str, Enum):
    """题目形态。

    两种形态的判题方式完全不同：

    * ``STDIO``  —— 竞赛题：程序读标准输入、写标准输出，比对输出文本。
    * ``ASSERT`` —— 函数补全题（HumanEval / MBPP）：提交只需实现指定函数，
      由测试代码调用它并断言结果。
    """

    STDIO = "stdio"
    ASSERT = "assert"


@dataclass(frozen=True)
class TestCase:
    """一条测试用例。"""

    id: str
    stdin: str = ""
    expected_stdout: str = ""
    assert_code: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        d: dict = {"id": self.id}
        if self.stdin:
            d["stdin"] = self.stdin
        if self.expected_stdout:
            d["expected_stdout"] = self.expected_stdout
        if self.assert_code:
            d["assert_code"] = self.assert_code
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TestCase:
        return cls(
            id=str(d["id"]),
            stdin=d.get("stdin", ""),
            expected_stdout=d.get("expected_stdout", ""),
            assert_code=d.get("assert_code", ""),
            note=d.get("note", ""),
        )


@dataclass
class Problem:
    """一道题：题面、限制、测试用例。"""

    id: str
    title: str = ""
    statement: str = ""
    kind: ProblemKind = ProblemKind.STDIO
    entry_point: str = ""
    time_limit_ms: int = 2000
    memory_limit_mb: int = 256
    checker: Checker = Checker.TOKEN
    float_tolerance: float = 1e-6
    tags: list[str] = field(default_factory=list)
    difficulty: str = ""
    source: str = ""
    metadata: dict = field(default_factory=dict)
    cases: list[TestCase] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.cases:
            raise ValueError(f"题目 {self.id} 没有任何测试用例，无法判题")
        seen: set[str] = set()
        for c in self.cases:
            if c.id in seen:
                raise ValueError(f"题目 {self.id} 存在重复的用例 id：{c.id}")
            seen.add(c.id)
        if self.kind is ProblemKind.ASSERT:
            if not self.entry_point:
                raise ValueError(f"函数补全题 {self.id} 必须指定 entry_point")
            missing = [c.id for c in self.cases if not c.assert_code.strip()]
            if missing:
                raise ValueError(f"题目 {self.id} 的用例缺少 assert_code：{missing}")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "statement": self.statement,
            "kind": self.kind.value,
            "entry_point": self.entry_point,
            "time_limit_ms": self.time_limit_ms,
            "memory_limit_mb": self.memory_limit_mb,
            "checker": self.checker.value,
            "float_tolerance": self.float_tolerance,
            "tags": list(self.tags),
            "difficulty": self.difficulty,
            "source": self.source,
            "metadata": dict(self.metadata),
            "cases": [c.to_dict() for c in self.cases],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Problem:
        return cls(
            id=str(d["id"]),
            title=d.get("title", ""),
            statement=d.get("statement", ""),
            kind=ProblemKind(d.get("kind", "stdio")),
            entry_point=d.get("entry_point", ""),
            time_limit_ms=int(d.get("time_limit_ms", 2000)),
            memory_limit_mb=int(d.get("memory_limit_mb", 256)),
            checker=Checker(d.get("checker", "token")),
            float_tolerance=float(d.get("float_tolerance", 1e-6)),
            tags=list(d.get("tags", [])),
            difficulty=d.get("difficulty", ""),
            source=d.get("source", ""),
            metadata=dict(d.get("metadata", {})),
            cases=[TestCase.from_dict(c) for c in d.get("cases", [])],
        )


@dataclass
class CaseResult:
    """单条用例的执行与判定结果。"""

    case_id: str
    verdict: Verdict
    expected_stdout: str = ""
    stdout: str = ""
    stderr: str = ""
    time_ms: int = 0
    cpu_ms: int = 0
    memory_kb: int = 0
    exit_code: int | None = None
    signal_name: str = ""
    #: 该用例的失败很可能是前面用例失败的连带结果（状态被改坏），
    #: 统计错因时应予过滤，避免一道题被算成十几个错因。
    cascaded: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "verdict": self.verdict.value,
            "expected_stdout": self.expected_stdout,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "time_ms": self.time_ms,
            "cpu_ms": self.cpu_ms,
            "memory_kb": self.memory_kb,
            "exit_code": self.exit_code,
            "signal_name": self.signal_name,
            "cascaded": self.cascaded,
            "detail": self.detail,
        }


@dataclass
class RunReport:
    """一次完整判题的结果。"""

    verdict: Verdict
    language: str = ""
    cases: list[CaseResult] = field(default_factory=list)
    compile_error: str = ""
    compile_ms: int = 0
    workdir: str = ""
    sandbox: str = ""

    @property
    def total_count(self) -> int:
        return len(self.cases)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.cases if c.verdict.is_accepted)

    @property
    def failed_cases(self) -> list[CaseResult]:
        return [c for c in self.cases if not c.verdict.is_accepted]

    @property
    def first_failure(self) -> CaseResult | None:
        failed = self.failed_cases
        return failed[0] if failed else None

    def summary(self) -> str:
        if self.verdict is Verdict.CE:
            return f"CE 编译失败\n{self.compile_error.strip()[:400]}"
        head = f"{self.verdict.value}  通过 {self.passed_count}/{self.total_count}"
        first = self.first_failure
        if first is not None:
            head += f"  首个失败用例：{first.case_id}"
            if first.detail:
                head += f"（{first.detail}）"
        return head

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "language": self.language,
            "sandbox": self.sandbox,
            "passed": self.passed_count,
            "total": self.total_count,
            "compile_ms": self.compile_ms,
            "compile_error": self.compile_error,
            "workdir": self.workdir,
            "cases": [c.to_dict() for c in self.cases],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
