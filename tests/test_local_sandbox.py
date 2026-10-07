from __future__ import annotations

import sys

from code_judge.sandbox.base import ExecLimits, ExecRequest
from code_judge.sandbox.local import LocalSandbox

PY = sys.executable


def run(code: str, **limit_kwargs) -> "object":
    sandbox = LocalSandbox()
    limits = ExecLimits(**limit_kwargs)
    return sandbox.exec(ExecRequest(argv=(PY, "-c", code), workdir="/tmp", limits=limits))


def test_normal_run_reports_wall_time_and_memory():
    result = run("print('hello')")
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.time_ms >= 0
    assert result.memory_kb > 0, "应该能从 wait4 拿到峰值内存"
    assert not result.timed_out
    assert not result.error


def test_missing_command_is_reported_not_raised():
    sandbox = LocalSandbox()
    result = sandbox.exec(
        ExecRequest(argv=("definitely-not-a-real-binary-xyz",), workdir="/tmp")
    )
    assert result.exit_code == 127
    assert "命令不存在" in result.error


def test_large_output_does_not_deadlock_and_is_truncated():
    """曾经的坑：先 wait 再读会卡死；这里验证并发读 + 截断都生效。"""
    result = run("import sys; sys.stdout.write('x' * 5_000_000)", max_output_bytes=10_000)
    assert result.exit_code == 0
    assert result.output_truncated
    assert len(result.stdout) == 10_000


def test_cpu_limit_stops_cpu_burn():
    """死循环烧 CPU：由 RLIMIT_CPU 打断，比挂钟兜底更早、更准。"""
    result = run("while True: pass", time_ms=600, max_output_bytes=1000)
    assert result.signal_name == "SIGXCPU", f"期望被 CPU 限制打断，实际 {result.signal_name}"
    assert not result.timed_out


def test_wall_timeout_stops_process_that_burns_no_cpu():
    """sleep 不耗 CPU，CPU 限制管不到，必须靠挂钟兜底。"""
    result = run("import time; time.sleep(30)", time_ms=400, max_output_bytes=1000)
    assert result.timed_out
    assert result.signal_name == "SIGKILL"


def test_crash_by_signal_is_reported():
    result = run("import ctypes; ctypes.string_at(0)", max_output_bytes=1000)
    assert result.signal_name in {"SIGSEGV", "SIGBUS", "SIGABRT"}


def test_stdin_is_delivered():
    result = run("import sys; print(sys.stdin.read().strip().upper())")
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_nonzero_exit_code_and_stderr_are_captured():
    result = run("import sys; sys.stderr.write('boom'); sys.exit(3)")
    assert result.exit_code == 3
    assert "boom" in result.stderr
