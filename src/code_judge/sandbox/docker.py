"""Docker 容器沙箱。

比本地沙箱多出来的三件事：

* **网络隔离**：``--network none``，选手代码无法外联
* **真正的内存上限**：cgroup 约束，本地 macOS 做不到（见 local.py 的说明）
* **进程数与 CPU 限额**：``--pids-limit`` / ``--cpus`` 由内核兜底

注意：macOS 上 Docker 只能挂载共享目录，因此工作目录必须放在默认共享的
路径下（本项目的 verifier 默认用 ``/tmp/code_judge_work``）。
"""

from __future__ import annotations

import subprocess
import uuid

from code_judge.sandbox.base import ExecRequest, ExecResult, Sandbox
from code_judge.sandbox._proc import run_process


class DockerSandbox(Sandbox):
    """基于 docker run 的容器沙箱。"""

    name = "docker"
    enforces_memory = True
    enforces_network_isolation = True

    def __init__(self, image: str = "python:3.11-slim", docker_bin: str = "docker") -> None:
        self.image = image
        self.docker_bin = docker_bin
        self._probe: tuple[bool, str] | None = None

    # -- 可用性 ---------------------------------------------------------------

    def available(self) -> tuple[bool, str]:
        if self._probe is None:
            self._probe = self._probe_daemon()
        return self._probe

    def _probe_daemon(self) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                [self.docker_bin, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                timeout=8,
                text=True,
            )
        except FileNotFoundError:
            return False, "未安装 docker CLI"
        except subprocess.TimeoutExpired:
            return False, "docker daemon 无响应（超时 8s）"
        if proc.returncode != 0:
            first_line = (proc.stderr or "").strip().splitlines()
            hint = first_line[-1] if first_line else "未知错误"
            return False, f"docker daemon 未运行：{hint}"
        return True, f"docker {proc.stdout.strip()}"

    # -- 执行 -----------------------------------------------------------------

    def exec(self, request: ExecRequest) -> ExecResult:
        ok, reason = self.available()
        if not ok:
            return ExecResult(error=f"Docker 沙箱不可用：{reason}", exit_code=127)

        limits = request.limits
        name = f"codejudge-{uuid.uuid4().hex[:12]}"
        container_cmd = [
            self.docker_bin,
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            f"{limits.memory_mb}m",
            "--memory-swap",
            f"{limits.memory_mb}m",
            "--pids-limit",
            str(limits.max_processes),
            "-v",
            f"{request.workdir}:/workspace",
            "-w",
            "/workspace",
        ]
        for key, value in (request.env or {}).items():
            container_cmd += ["-e", f"{key}={value}"]
        container_cmd.append(self.image)
        container_cmd += list(request.argv)

        def kill_container() -> None:
            subprocess.run(
                [self.docker_bin, "kill", name],
                capture_output=True,
                timeout=10,
            )

        # 给 docker CLI 留出比程序本身更宽裕的时间，让它有机会把容器的
        # 资源统计和安全退出码带回来
        outcome = run_process(
            container_cmd,
            stdin=request.stdin,
            workdir=request.workdir,
            limits=limits,
            env=None,
            on_timeout=kill_container,
        )
        result = outcome.to_exec_result(memory_limit_enforced=True)
        if result.exit_code == 137 and not result.timed_out:
            # 137 = SIGKILL，容器里通常意味着 OOM 或被杀
            result.stderr = (result.stderr + "\n[容器以 137 退出，可能触发内存上限]").strip()
        return result

    def describe(self) -> dict:
        info = super().describe()
        info["image"] = self.image
        return info
