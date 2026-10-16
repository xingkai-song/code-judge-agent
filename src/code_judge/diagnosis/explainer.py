"""把诊断结论转成给人看的讲解。

讲解只使用诊断过程中**已经被验证的事实**（失败用例、期望值、实际值、
被实跑验证过的修复），不做任何推测性的发挥。

找不到可验证修复时，讲解会明确说「未定位」，并把已知症状列出来——
**宁可承认不知道，也不编一个听起来合理的理由**。这一点对教学场景尤其重要：
错误的知识比没有知识更糟。

（这里用的是模板生成，保证输出稳定、可测。要换成大模型生成时，把这里的
证据包成 prompt 即可，接口不用变；但无论谁来写讲解，最终都必须过一遍
反向验证这一关。）
"""

from __future__ import annotations

from code_judge.diagnosis.models import Diagnosis


def explain(diagnosis: Diagnosis) -> str:
    lines: list[str] = []

    if diagnosis.submitted_verdict == "AC":
        return "提交通过了全部测试用例，没有发现错误。"

    symptom = diagnosis.symptom

    # 代码根本不是合法程序时，不猜、不解释，直接把编译器/解释器的原话转给学生——
    # 那里面已经有行号、位置和原因，比任何推测都准。
    if symptom.get("compile_error"):
        return "\n".join([
            "编译没有通过。编译器已经把位置和原因说清楚了：",
            "",
            str(symptom["compile_error"]).strip(),
        ])
    if symptom.get("syntax_error"):
        return "\n".join([
            "这段代码没有通过 Python 的语法检查，解释器的原话是：",
            "",
            str(symptom["syntax_error"]).strip(),
            "",
            "先把这一处改对，再提交一次就能继续往下分析了。",
        ])

    if diagnosis.verified and diagnosis.repairs:
        repair = diagnosis.repairs[0]
        lines.append(f"诊断：{diagnosis.fault_label}（已通过判题机验证）")
        lines.append(f"位置：第 {repair.lineno} 行")
        lines.append(f"改动：{repair.before}  →  {repair.after}")
        lines.append("")
        lines.append(
            f"依据：用例 {symptom.get('failed_case', '?')} 失败"
            f"（{symptom.get('detail') or '结果不符'}）。"
            f"把这一处改成上面的写法后，{repair.passed}/{repair.total} 条用例全部通过。"
        )
        if len(diagnosis.repairs) > 1:
            others = diagnosis.repairs[1:]
            lines.append(
                f"另有 {len(others)} 个改法也能让全部用例通过，说明这道题存在多种可行实现，"
                "上面的改法改动最小。"
            )
        lines.append("")
        lines.append("说明：这个结论不是猜的——系统真的把那处代码改掉、重跑了一遍所有测试用例。")
    else:
        lines.append("诊断：未能定位到可验证的单点错误")
        lines.append("")
        lines.append("已知线索：")
        lines.append(
            f"  · 用例 {symptom.get('failed_case', '?')} 失败"
            f"（{symptom.get('detail') or '结果不符'}）"
        )
        if symptom.get("expected") or symptom.get("actual"):
            lines.append(f"  · 期望输出：{symptom.get('expected') or '（空）'}")
            lines.append(f"  · 实际输出：{symptom.get('actual') or '（空）'}")
        if symptom.get("stderr_tail"):
            lines.append(f"  · 运行错误：{symptom['stderr_tail']}")
        lines.append("")

        # 定位不到时要给「错误的形状」：这些是从失败用例里直接读出来的事实，
        # 不是对错因的猜测。学生拿不到「改哪一行」，至少能知道「错成什么样」。
        pattern = symptom.get("pattern") or []
        if pattern:
            lines.append("从失败的样子能看出：")
            for note in pattern:
                lines.append(f"  · {note}")
            lines.append("")

        if diagnosis.candidates_total == 0:
            if pattern:
                lines.append("系统在这份代码里找不到「只改一处」就能修好的地方，所以不做猜测。")
            else:
                lines.append(
                    "这份代码里没有系统能识别的单点错误模式（比如比较符写反、常量差一）。"
                    "错误更可能是结构性的（算法思路、数据结构选择），"
                    "这种情况系统不做猜测。"
                )
        else:
            lines.append(
                f"系统试了 {diagnosis.candidates_tried} 个单点修复候选都没能让全部用例通过。"
                "这说明错误可能不止一处，或者需要调整整体思路——"
                "这种时候给一个「看起来合理」的结论反而会误导，所以这里不做猜测。"
            )

    return "\n".join(lines)
