"""输出比对。

判题里一个容易被低估的细节：选手输出的行尾空格、末尾多余的换行、
Windows 换行符都不应该判错。这一层负责把这些差异归一化掉，
同时保留「切分方式」和「比对方式」的区分，方便后续按题目配置。
"""

from __future__ import annotations

from code_judge.types import Checker


def normalize(text: str) -> str:
    """归一化：统一换行符、去掉每行行尾空白、去掉末尾空行。

    不改变行内空白与行首缩进，这些差异仍然算错。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _split_tokens(text: str) -> list[str]:
    return normalize(text).split()


def compare_exact(actual: str, expected: str) -> tuple[bool, str]:
    a, e = normalize(actual), normalize(expected)
    if a == e:
        return True, ""
    return False, _diff_detail(a, e)


def compare_token(actual: str, expected: str) -> tuple[bool, str]:
    a, e = _split_tokens(actual), _split_tokens(expected)
    if a == e:
        return True, ""
    if len(a) != len(e):
        return False, f"输出项数不符：期望 {len(e)} 项，实际 {len(a)} 项"
    for i, (x, y) in enumerate(zip(a, e)):
        if x != y:
            return False, f"第 {i + 1} 项不符：期望 {_clip(y)}，实际 {_clip(x)}"
    return False, "输出不一致"


def compare_float(actual: str, expected: str, tolerance: float = 1e-6) -> tuple[bool, str]:
    a, e = _split_tokens(actual), _split_tokens(expected)
    if len(a) != len(e):
        return False, f"输出项数不符：期望 {len(e)} 项，实际 {len(a)} 项"
    for i, (x, y) in enumerate(zip(a, e)):
        try:
            fx, fy = float(x), float(y)
        except ValueError:
            return False, f"第 {i + 1} 项不是合法数字：期望 {_clip(y)}，实际 {_clip(x)}"
        # 绝对误差 + 相对误差，避免大数下容差失效
        if abs(fx - fy) > tolerance * max(1.0, abs(fy)):
            return False, f"第 {i + 1} 项超出容差：期望 {_clip(y)}，实际 {_clip(x)}"
    return True, ""


def compare(
    actual: str,
    expected: str,
    checker: Checker = Checker.TOKEN,
    tolerance: float = 1e-6,
) -> tuple[bool, str]:
    """统一的比对入口，返回 (是否一致, 不一致的原因)。"""
    if checker is Checker.EXACT:
        return compare_exact(actual, expected)
    if checker is Checker.FLOAT:
        return compare_float(actual, expected, tolerance)
    return compare_token(actual, expected)


def _clip(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _diff_detail(actual: str, expected: str) -> str:
    a_lines, e_lines = actual.split("\n"), expected.split("\n")
    if len(a_lines) != len(e_lines):
        return f"行数不符：期望 {len(e_lines)} 行，实际 {len(a_lines)} 行"
    for i, (x, y) in enumerate(zip(a_lines, e_lines)):
        if x != y:
            return f"第 {i + 1} 行不符：期望 {_clip(y)!r}，实际 {_clip(x)!r}"
    return "输出不一致"
