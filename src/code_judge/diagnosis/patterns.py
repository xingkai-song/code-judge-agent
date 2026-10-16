"""从失败用例的模式里读「错误的形状」。

定位不到的时候，判题结果本身还留着大量信息：哪几条用例挂了、它们的期望输出是
什么、耗时多少、抛了什么异常。这些信息不足以指出「该改哪一行」，但足以说明
「错成什么样」——是整体没做对，还是漏了一类情况；是逻辑错，还是数据一大就超时。

这一层刻意**只陈述能从结果里直接读出来的事实**，不做「你可能需要换算法」这类
没有证据的推测。原则和诊断一致：宁可少说，不说错。
"""

from __future__ import annotations

from code_judge.types import CaseResult, Problem, RunReport, Verdict


def analyze(report: RunReport, problem: Problem) -> list[str]:
    """返回若干条「错误形状」的观察，给讲解层直接用。"""
    cases = report.cases
    if not cases:
        return []
    failed = [c for c in cases if not c.verdict.is_accepted]
    passed = [c for c in cases if c.verdict.is_accepted]
    if not failed:
        return []

    notes: list[str] = []

    # 一、整体还是局部：全挂通常意味着思路层面就有问题
    if not passed:
        notes.append(f"{len(cases)} 条用例一条都没过——问题更可能在整体思路上，而不是某处细节。")
    else:
        notes.append(f"{len(cases)} 条用例里 {len(passed)} 条通过、{len(failed)} 条失败。")

    # 二、失败用例有没有共同的期望输出（说明「漏了一整类情况」）
    only_failed_expects = _single_expectation(failed)
    if only_failed_expects is not None and passed:
        passed_expects = {c.expected_stdout.strip() for c in passed}
        if only_failed_expects not in passed_expects:
            notes.append(
                f"失败的用例期望输出全都是 {_clip(only_failed_expects, 40)}，"
                "而通过的用例期望输出都不同——看起来是有一整类情况没处理到。"
            )

    # 三、超时：一半能跑完一半超时，基本就是效率问题
    timeouts = [c for c in failed if c.verdict is Verdict.TLE]
    if timeouts and passed:
        notes.append(
            f"其中 {len(timeouts)} 条是超时，其它用例能正常跑完——"
            f"数据量变大时就跑不动了（时限 {problem.time_limit_ms} ms），"
            "检查有没有多余的多层循环。"
        )
    elif timeouts:
        notes.append(f"失败的用例里有 {len(timeouts)} 条超时（时限 {problem.time_limit_ms} ms）。")

    # 四、运行时报错：解释器已经说得很具体了，直接转述
    errors = [c for c in failed if c.verdict is Verdict.RE]
    if errors:
        detail = _last_line(errors[0].stderr) or errors[0].detail
        if detail:
            notes.append(f"运行时报错：{_clip(detail, 120)}")

    # 五、挂了但完全没输出：程序连结果都没算出来
    if all(not c.stdout.strip() for c in failed) and not timeouts:
        notes.append("失败的用例里程序一个字符都没输出——它连结果都没算出来。")

    return notes


def _single_expectation(cases: list[CaseResult]) -> str | None:
    expects = {c.expected_stdout.strip() for c in cases}
    return next(iter(expects)) if len(expects) == 1 else None


def _last_line(text: str) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"
