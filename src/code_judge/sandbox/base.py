"""沙箱的接口定义。

设计原则：沙箱只负责「安全地跑一个命令，并把资源消耗如实报回来」，
不做任何判题判断。判定逻辑全部留给 verifier，这样换沙箱不影响判题语义。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class SandboxUnavailable(RuntimeError):
    """沙箱在当前环境下不可用。"""


@dataclass(frozen=True)
class ExecLimits:
    """一次执行的资源上限。"""

    time_ms: int = 2000
    memory_mb: int = 256
    max_output_bytes: int = 1_000_000
    max_processes: int = 128
    file_size_bytes: int = 16 * 1024 * 1024

    @property
    def time_s(self) -> float:
        return self.time_ms / 1000.0

    @property
    def memory_bytes(self) -> int:
        return self.memory_mb * 1024 * 1024

    @property
    def wall_timeout_s(self) -> float:
        """看门狗兜底时间。

        刻意比 CPU 限制宽裕：正常死循环由 ``RLIMIT_CPU`` 及时打断，
        而 ``time.sleep`` 这类**不耗 CPU 却一直不结束**的程序只能靠挂钟兜底，
        两者分工不同，所以这里留出余量而不是等于 time limit。
        """
        return self.time_s * 2.0 + 0.5


@dataclass
class ExecRequest:
    """一次执行请求。"""

    argv: tuple[str, ...]
    stdin: str = ""
    workdir: str = "."
    limits: ExecLimits = field(default_factory=ExecLimits)
    env: dict[str, str] = field(default_factory=dict)
    label: str = ""


@dataclass
class ExecResult:
    """一次执行的实际结果（不做对错判断）。"""

    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    time_ms: int = 0
    cpu_ms: int = 0
    memory_kb: int = 0
    signal_name: str = ""
    timed_out: bool = False
    output_truncated: bool = False
    memory_limit_enforced: bool = False
    error: str = ""

    @property
    def crashed(self) -> bool:
        return self.exit_code not in (0, None) or bool(self.signal_name)

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "time_ms": self.time_ms,
            "cpu_ms": self.cpu_ms,
            "memory_kb": self.memory_kb,
            "signal_name": self.signal_name,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
            "memory_limit_enforced": self.memory_limit_enforced,
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class Sandbox(ABC):
    """沙箱接口。"""

    name: str = "sandbox"
    #: 该沙箱能否强制内存上限。本地沙箱在 macOS 上做不到，容器可以。
    enforces_memory: bool = False
    #: 该沙箱能否切断网络。
    enforces_network_isolation: bool = False

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """返回 (是否可用, 原因)。"""

    @abstractmethod
    def exec(self, request: ExecRequest) -> ExecResult:
        """执行命令并返回资源消耗，不抛业务异常。"""

    def describe(self) -> dict:
        return {
            "name": self.name,
            "enforces_memory": self.enforces_memory,
            "enforces_network_isolation": self.enforces_network_isolation,
        }
