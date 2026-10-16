"""诊断闭环。

流程：判题 → 生成修复候选 → 逐个实跑验证 → 得出结论。

关键取舍：**每个修复候选都要真跑一遍判题机**。这一步很贵（一个候选约 20–30ms），
但它是整个方案可信的来源——结论不是「模型觉得这里是边界错误」，而是
「把这里改掉之后，全部用例都过了」。

stdio 型题（完整程序）会把这份代价放大：每条用例都要单独起一次进程，一道题
挂 8 条用例，48 个候选就是几百次进程。所以候选验证走的是**快速判题器**——
遇到第一条失败用例就停。反正「有没有全部通过」这个结论不会因此改变，
但绝大多数错误候选在第一两条用例上就被淘汰了。

代价和收益都写在这里，方便面试时直接讲：

* 收益：诊断结论可验证、可复现，而且天然给出出错行号和具体改法。
* 代价：搜索成本随代码规模线性增长；而且只能发现「单点、且有对应逆向算子」的
  错误类型，多点错误或结构性错误会返回「未定位」。
"""

from __future__ import annotations

import re
import time

from code_judge.diagnosis.models import Diagnosis, Repair
from code_judge.diagnosis.patterns import analyze as analyze_failure_pattern
from code_judge.diagnosis.repairs import propose_repairs
from code_judge.types import CaseResult, Problem, RunReport, Verdict
from code_judge.verifier import CodeVerifier

#: Python 里表示「这段代码根本不是合法程序」的异常
_SYNTAX_MARKERS = ("SyntaxError", "IndentationError", "TabError")

#: 解释器报错里的绝对路径指向判题的临时工作目录，对学生没有任何意义
_FILE_PATH_RE = re.compile(r'File "[^"]*[/\\]([^/\\"]+)"')


class Diagnoser:
    """给定一份失败提交，定位错因并给出被验证过的修复。"""

    def __init__(
        self,
        verifier: CodeVerifier | None = None,
        max_per_operator: int = 6,
        max_candidates: int = 48,
        max_repairs: int = 1,
        exclude_operators: set[str] | None = None,
    ) -> None:
        self.verifier = verifier or CodeVerifier()
        self.max_per_operator = max_per_operator
        self.max_candidates = max_candidates
        self.max_repairs = max_repairs
        self.exclude_operators = set(exclude_operators or ())
        #: 候选验证专用的判题器：一条用例失败就停，不为「哪几条挂了」付出代价。
        #: 报告给学生的初始判题仍然用完整判题器（需要每一条用例的结果）。
        self.candidate_verifier = CodeVerifier(
            sandbox=self.verifier.sandbox,
            workdir_root=self.verifier.workdir_root,
            stop_on_first_failure=True,
            compile_time_ms=self.verifier.compile_time_ms,
        )

    def diagnose(
        self, source: str, problem: Problem, language: str = "python"
    ) -> Diagnosis:
        started = time.monotonic()
        report = self.verifier.verify(source, problem, language)
        diagnosis = Diagnosis(
            problem_id=problem.id,
            submitted_verdict=report.verdict.value,
            passed=report.passed_count,
            total=report.total_count,
            symptom=_describe_symptom(report, problem),
        )

        if report.verdict is Verdict.AC:
            diagnosis.verified = True
            diagnosis.fault_label = "无错误"
            diagnosis.note = "提交通过了全部用例"
            diagnosis.elapsed_ms = int((time.monotonic() - started) * 1000)
            return diagnosis

        # 代码压根不是合法程序时，在语法树上找修复候选既无意义（解析不出树），
        # 也没必要——编译器/解释器的报错本身就带行号和原因，比任何猜测都准。
        if _is_fatal_syntax(report):
            diagnosis.note = "代码没有通过编译或语法检查，直接转述编译器/解释器的诊断"
            diagnosis.elapsed_ms = int((time.monotonic() - started) * 1000)
            return diagnosis

        candidates = propose_repairs(
            source,
            max_per_operator=self.max_per_operator,
            max_total=self.max_candidates,
            exclude_operators=self.exclude_operators,
        )
        diagnosis.candidates_total = len(candidates)

        for candidate in candidates:
            diagnosis.candidates_tried += 1
            repaired = self.candidate_verifier.verify(candidate.source, problem, language)
            if repaired.verdict is not Verdict.AC:
                continue
            diagnosis.repairs.append(
                Repair(
                    operator=candidate.operator,
                    label=candidate.label,
                    lineno=candidate.lineno,
                    before=candidate.before,
                    after=candidate.after,
                    source=candidate.source,
                    verdict=repaired.verdict.value,
                    passed=repaired.passed_count,
                    total=repaired.total_count,
                )
            )
            if len(diagnosis.repairs) >= self.max_repairs:
                break

        if diagnosis.repairs:
            best = diagnosis.repairs[0]
            diagnosis.verified = True
            diagnosis.operator = best.operator
            diagnosis.fault_label = best.label
            diagnosis.lineno = best.lineno
        else:
            diagnosis.note = (
                f"试过 {diagnosis.candidates_tried} 个单点修复候选都没有通过，"
                "需要多处修改或结构性重写，无法给出可验证结论"
            )

        diagnosis.elapsed_ms = int((time.monotonic() - started) * 1000)
        return diagnosis


def _describe_symptom(report: RunReport, problem: Problem) -> dict:
    """把判题结果里最有诊断价值的信息抽出来。

    这些是「证据」：失败在哪个用例、期望什么、实际得到什么、抛了什么异常。
    讲解必须建立在它们之上，不能凭空生成。
    """
    symptom: dict = {"verdict": report.verdict.value}

    # 编译失败：编译器的话就是最准确的诊断，原样带上
    if report.verdict is Verdict.CE:
        symptom["compile_error"] = _clip(report.compile_error, 800)
        return symptom

    failure: CaseResult | None = report.first_failure
    if failure is None:
        return symptom

    syntax = _syntax_error_text(failure.stderr)
    if syntax:
        symptom["syntax_error"] = syntax

    symptom.update({
        "failed_case": failure.case_id,
        "detail": failure.detail,
        "expected": _clip(failure.expected_stdout),
        "actual": _clip(failure.stdout),
        "stderr_tail": _clip(_last_line(failure.stderr)),
        "signal": failure.signal_name,
        "exit_code": failure.exit_code,
    })

    # 定位不到的时候，失败的「形状」本身就是有用的信息
    pattern = analyze_failure_pattern(report, problem)
    if pattern and not syntax:
        symptom["pattern"] = pattern
    return symptom


def _is_fatal_syntax(report: RunReport) -> bool:
    """这批失败是「代码不合法」造成的，而不是「代码写错了」。"""
    if report.verdict is Verdict.CE:
        return True
    failure = report.first_failure
    return failure is not None and bool(_syntax_error_text(failure.stderr))


def _syntax_error_text(stderr: str) -> str:
    """从解释器的报错里取「文件、行号、箭头、异常类型」这几行。

    只取尾部几行：前面通常是无关的调用栈，最后四行才是真正的位置和原因。
    """
    text = (stderr or "").strip()
    if not text or not any(marker in text for marker in _SYNTAX_MARKERS):
        return ""
    tail = "\n".join(text.splitlines()[-6:]).strip()
    return _FILE_PATH_RE.sub(r'File "\1"', tail)


def _clip(text: str, limit: int = 160) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _last_line(text: str) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""
