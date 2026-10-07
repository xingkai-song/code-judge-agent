"""诊断闭环。

流程：判题 → 生成修复候选 → 逐个实跑验证 → 得出结论。

关键取舍：**每个修复候选都要真跑一遍判题机**。这一步很贵（一个候选约 20–30ms），
但它是整个方案可信的来源——结论不是「模型觉得这里是边界错误」，而是
「把这里改掉之后，全部用例都过了」。

代价和收益都写在这里，方便面试时直接讲：

* 收益：诊断结论可验证、可复现，而且天然给出出错行号和具体改法。
* 代价：搜索成本随代码规模线性增长；而且只能发现「单点、且有对应逆向算子」的
  错误类型，多点错误或结构性错误会返回「未定位」。
"""

from __future__ import annotations

import time

from code_judge.diagnosis.models import Diagnosis, Repair
from code_judge.diagnosis.repairs import propose_repairs
from code_judge.types import CaseResult, Problem, RunReport, Verdict
from code_judge.verifier import CodeVerifier


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
            symptom=_describe_symptom(report),
        )

        if report.verdict is Verdict.AC:
            diagnosis.verified = True
            diagnosis.fault_label = "无错误"
            diagnosis.note = "提交通过了全部用例"
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
            repaired = self.verifier.verify(candidate.source, problem, language)
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


def _describe_symptom(report: RunReport) -> dict:
    """把判题结果里最有诊断价值的信息抽出来。

    这些是「证据」：失败在哪个用例、期望什么、实际得到什么、抛了什么异常。
    讲解必须建立在它们之上，不能凭空生成。
    """
    failure: CaseResult | None = report.first_failure
    if failure is None:
        return {"verdict": report.verdict.value}
    return {
        "verdict": report.verdict.value,
        "failed_case": failure.case_id,
        "detail": failure.detail,
        "expected": _clip(failure.expected_stdout),
        "actual": _clip(failure.stdout),
        "stderr_tail": _clip(_last_line(failure.stderr)),
        "signal": failure.signal_name,
        "exit_code": failure.exit_code,
    }


def _clip(text: str, limit: int = 160) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _last_line(text: str) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""
