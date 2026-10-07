"""子进程执行的公共机制：并发读管道、看门狗超时、rusage 采集。

LocalSandbox 和 DockerSandbox 都复用这里，区别只在「启动什么命令」和
「要不要在子进程里设资源上限」。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from code_judge.sandbox.base import ExecLimits, ExecResult

#: ru_maxrss 的单位不一致：darwin 给的是字节，Linux 给的是 KB。统一换算成 KB。
_RSS_DIVISOR = 1024 if sys.platform == "darwin" else 1

#: 只在需要用 preexec_fn 设资源上限时加锁。
#: preexec_fn 在 fork 之后、exec 之前执行，多线程并发 fork 时子进程可能继承到
#: 别的线程持有的锁而死锁。加锁能大幅降低这个概率（本地沙箱只跑可信代码，
#: 真正的隔离交给容器，所以这里取「够用」而不是「万无一失」）。
_SPAWN_LOCK = threading.Lock()


@dataclass
class ProcessOutcome:
    exit_code: int | None
    stdout: str
    stderr: str
    time_ms: int
    cpu_ms: int
    memory_kb: int
    signal_name: str
    timed_out: bool
    output_truncated: bool
    error: str = ""

    def to_exec_result(self, memory_limit_enforced: bool) -> ExecResult:
        return ExecResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            time_ms=self.time_ms,
            cpu_ms=self.cpu_ms,
            memory_kb=self.memory_kb,
            signal_name=self.signal_name,
            timed_out=self.timed_out,
            output_truncated=self.output_truncated,
            memory_limit_enforced=memory_limit_enforced,
            error=self.error,
        )


def run_process(
    argv: list[str],
    *,
    stdin: str,
    workdir: str,
    limits: ExecLimits,
    env: dict[str, str] | None = None,
    preexec_fn=None,
    on_timeout=None,
) -> ProcessOutcome:
    """跑一个命令，并发读输出，超时杀进程组，返回真实资源消耗。

    :param on_timeout: 超时时额外执行的清理动作（例如杀掉同名容器）。
    """
    started = time.monotonic()
    try:
        if preexec_fn is not None:
            with _SPAWN_LOCK:
                proc = _spawn(argv, workdir, env, preexec_fn)
        else:
            proc = _spawn(argv, workdir, env, None)
    except OSError as exc:
        return ProcessOutcome(
            exit_code=126,
            stdout="",
            stderr="",
            time_ms=0,
            cpu_ms=0,
            memory_kb=0,
            signal_name="",
            timed_out=False,
            output_truncated=False,
            error=f"启动失败：{exc}",
        )

    cap = limits.max_output_bytes
    readers = [_BoundedReader(proc.stdout, cap), _BoundedReader(proc.stderr, cap)]
    for reader in readers:
        reader.start()

    state = {"timed_out": False}
    stop_watchdog = threading.Event()
    threading.Thread(
        target=_watchdog,
        args=(proc.pid, limits.wall_timeout_s, stop_watchdog, state, on_timeout),
        daemon=True,
    ).start()
    threading.Thread(target=_feed_stdin, args=(proc.stdin, stdin), daemon=True).start()

    try:
        _pid, status, usage = os.wait4(proc.pid, 0)
    except ChildProcessError:
        stop_watchdog.set()
        return ProcessOutcome(
            exit_code=None,
            stdout="",
            stderr="",
            time_ms=0,
            cpu_ms=0,
            memory_kb=0,
            signal_name="",
            timed_out=False,
            output_truncated=False,
            error="子进程已被回收",
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    stop_watchdog.set()
    proc.returncode = os.waitstatus_to_exitcode(status)  # 避免 Popen 再次 wait

    for reader in readers:
        reader.join(timeout=0.5)
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass

    signal_name = ""
    if os.WIFSIGNALED(status):
        signum = os.WTERMSIG(status)
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = f"SIG{signum}"

    return ProcessOutcome(
        exit_code=proc.returncode,
        stdout=readers[0].text(),
        stderr=readers[1].text(),
        time_ms=elapsed_ms,
        cpu_ms=int((usage.ru_utime + usage.ru_stime) * 1000),
        memory_kb=int(usage.ru_maxrss / _RSS_DIVISOR),
        signal_name=signal_name,
        timed_out=state["timed_out"],
        output_truncated=any(r.truncated for r in readers),
    )


def _spawn(argv: list[str], workdir: str, env, preexec_fn):
    return subprocess.Popen(
        argv,
        cwd=workdir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
        preexec_fn=preexec_fn,
    )


def _watchdog(pid: int, timeout_s: float, stop: threading.Event, state: dict, on_timeout) -> None:
    if stop.wait(timeout_s):
        return
    state["timed_out"] = True
    if on_timeout is not None:
        try:
            on_timeout()
        except Exception:  # 清理失败不能影响主流程
            pass
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _feed_stdin(stream, data: str) -> None:
    if stream is None:
        return
    try:
        if data:
            stream.write(data.encode("utf-8", errors="replace"))
        stream.flush()
    except (BrokenPipeError, ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except (BrokenPipeError, OSError):
            pass


class _BoundedReader(threading.Thread):
    """并发读取管道，最多保留 max_bytes，超出部分丢弃并标记截断。"""

    def __init__(self, stream, max_bytes: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._max = max_bytes
        self._chunks: list[bytes] = []
        self._size = 0
        self.truncated = False

    def run(self) -> None:
        if self._stream is None:
            return
        try:
            while True:
                block = self._stream.read(8192)
                if not block:
                    break
                room = self._max - self._size
                if room <= 0:
                    self.truncated = True
                    continue
                if len(block) > room:
                    self._chunks.append(block[:room])
                    self._size += room
                    self.truncated = True
                else:
                    self._chunks.append(block)
                    self._size += len(block)
        except (OSError, ValueError):
            pass

    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="replace")
