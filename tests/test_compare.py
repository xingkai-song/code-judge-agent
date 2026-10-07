from __future__ import annotations

from code_judge.compare import compare, compare_float, normalize
from code_judge.types import Checker


def test_normalize_strips_trailing_whitespace_and_blank_lines():
    assert normalize("1 2   \n3\n\n\n") == "1 2\n3"
    assert normalize("a\r\nb\r\n") == "a\nb"
    # 行首缩进是有意义的，不能被吃掉
    assert normalize("  x\n") == "  x"


def test_token_checker_ignores_spacing_and_line_breaks():
    ok, _ = compare("1\n2\n3\n", "1 2 3", Checker.TOKEN)
    assert ok


def test_token_checker_reports_position_and_values():
    ok, detail = compare("1 2 9", "1 2 3", Checker.TOKEN)
    assert not ok
    assert "第 3 项" in detail
    assert "3" in detail and "9" in detail


def test_token_checker_detects_missing_output():
    ok, detail = compare("", "42", Checker.TOKEN)
    assert not ok
    assert "项数" in detail


def test_exact_checker_is_stricter_than_token():
    assert compare("1 2", "1  2", Checker.EXACT)[0] is False
    assert compare("1 2", "1  2", Checker.TOKEN)[0] is True
    # 但末尾换行不该算错
    assert compare("1 2\n", "1 2", Checker.EXACT)[0] is True


def test_float_checker_accepts_within_tolerance():
    ok, _ = compare_float("0.3333333333", "0.3333333334", 1e-6)
    assert ok, "绝对误差 1e-10 应该通过"
    ok, detail = compare_float("0.4", "0.333333", 1e-6)
    assert not ok
    assert "容差" in detail


def test_float_checker_uses_relative_tolerance_for_large_numbers():
    # 1e9 级别的数，绝对误差 1e-3 应该通过（相对误差 1e-12）
    ok, _ = compare_float("1000000000.001", "1000000000.0", 1e-6)
    assert ok
    # 同样的绝对误差在小数上就该失败
    ok, _ = compare_float("0.101", "0.1", 1e-6)
    assert not ok


def test_float_checker_rejects_non_numeric():
    ok, detail = compare_float("abc", "1.0", 1e-6)
    assert not ok
    assert "不是合法数字" in detail
