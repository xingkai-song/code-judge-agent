"""本地子进程沙箱。

用途：开发、单元测试、离线评测。生产判题请用 DockerSandbox。

实现里踩过的三个坑（面试可以直接讲）：

1. **管道死锁**：如果先 ``wait()`` 再读输出，子进程写满 64KB 管道缓冲区后会
   阻塞在 write 上，父进程又在 wait 上等它退出——双方互等，整个判题卡死。
   所以必须开**读线程**并发消费 stdout/stderr。选手打印一大段调试信息时
   必然触发这个坑。
2. **macOS 无法限制地址空间**：``setrlimit(RLIMIT_AS)`` 在新上限低于当前已用
   虚拟内存时会直接失败（EINVAL），而 Python 进程的虚拟内存本来就很大，
   所以本地沙箱**无法真正强制内存上限**，只能如实上报峰值。
   真正的内存限制只能靠容器 cgroup。
3. **进程组**：子进程用 ``start_new_session=True`` 独立成组，超时后对整组发
   SIGKILL，否则子进程 fork 出来的后代（死循环、fork 炸弹）会活下来。
4. **别用 RLIMIT_NPROC 防 fork 炸弹**：它是按**用户**统计进程总数的，不是按
   进程树。设成 128 之后，机器上已有进程数一旦超过这个值，子进程再 fork 就
   会失败——实测表现是编译器报 ``posix_spawn failed: Resource temporarily
   unavailable``，C++ 提交全部编译失败。防 fork 炸弹要靠容器的 ``--pids-limit``
   （按 cgroup 统计，语义才对）。所以本地沙箱**不做进程数限制**，只用于跑
   可信代码；不可信代码一律走 Docker。
"""

from __future__ import annotations

import math
import os
import resource
import shutil
import sys

from code_judge.sandbox._proc import run_process
from code_judge.sandbox.base import ExecLimits, ExecRequest, ExecResult, Sandbox

_MINIMAL_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}


class LocalSandbox(Sandbox):
    """本机子进程沙箱。"""

    name = "local"
    enforces_memory = sys.platform.startswith("linux")
    enforces_network_isolation = False

    def available(self) -> tuple[bool, str]:
        return True, "本机子进程执行"

    def exec(self, request: ExecRequest) -> ExecResult:
        argv = list(request.argv)
        if not _program_exists(argv[0], request.workdir):
            return ExecResult(error=f"命令不存在：{argv[0]}", exit_code=127)

        env = dict(_MINIMAL_ENV)
        env.update(request.env)

        outcome = run_process(
            argv,
            stdin=request.stdin,
            workdir=request.workdir,
            limits=request.limits,
            env=env,
            preexec_fn=_limit_setter(request.limits),
        )
        return outcome.to_exec_result(memory_limit_enforced=self.enforces_memory)


def _limit_setter(limits: ExecLimits):
    """返回只在子进程里执行的函数，用于设置资源上限。

    任何一项失败都不能让子进程起不来——本地沙箱的限制是尽力而为，
    失败就跳过，强隔离交给容器。
    """
    cpu_soft = max(1, int(math.ceil(limits.time_s)))
    cpu_hard = cpu_soft + 1

    def _apply() -> None:
        _try_set(resource.RLIMIT_CPU, (cpu_soft, cpu_hard))
        _try_set(resource.RLIMIT_FSIZE, (limits.file_size_bytes, limits.file_size_bytes))
        _try_set(resource.RLIMIT_NOFILE, (256, 256))
        _try_set(resource.RLIMIT_CORE, (0, 0))  # 不要写 core dump
        if sys.platform.startswith("linux"):
            _try_set(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))

    return _apply


def _try_set(which: int, value: tuple[int, int]) -> None:
    try:
        soft, hard = resource.getrlimit(which)
        resource.setrlimit(which, (min(soft, value[0]), min(hard, value[1])))
    except (ValueError, OSError):
        pass


def _program_exists(program: str, workdir: str) -> bool:
    """判断可执行文件是否存在。

    编译产物是相对路径（``./main``），必须相对**沙箱工作目录**去查，
    而不是父进程的当前目录——否则 C++ 提交会全部误报「命令不存在」。
    """
    if os.path.isabs(program):
        return os.path.exists(program)
    if program.startswith(".") or os.sep in program:
        return os.path.exists(os.path.join(workdir, program))
    return shutil.which(program) is not None
