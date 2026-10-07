"""执行沙箱。

两种实现：

* ``LocalSandbox``  —— 本机子进程 + 资源限制。零依赖，用于开发和离线测试。
* ``DockerSandbox`` —— 容器隔离，网络、内存、进程数都由内核兜底，用于正式判题。

两者对上层暴露同一个接口，判题器不关心底下用的是哪一个。
"""

from code_judge.sandbox.base import (
    ExecLimits,
    ExecRequest,
    ExecResult,
    Sandbox,
    SandboxUnavailable,
)
from code_judge.sandbox.local import LocalSandbox

__all__ = [
    "DockerSandbox",
    "ExecLimits",
    "ExecRequest",
    "ExecResult",
    "LocalSandbox",
    "Sandbox",
    "SandboxUnavailable",
    "default_sandbox",
]


def __getattr__(name: str):
    # DockerSandbox 依赖 docker CLI，放在函数里延迟导入，避免无 Docker 环境导入失败
    if name == "DockerSandbox":
        from code_judge.sandbox.docker import DockerSandbox

        return DockerSandbox
    raise AttributeError(name)


def default_sandbox(prefer: str = "auto") -> Sandbox:
    """按偏好挑一个可用沙箱。

    ``prefer="docker"`` 时若 Docker 不可用会直接报错；
    ``prefer="auto"`` 时可用则用 Docker，否则退回本地。
    """
    from code_judge.sandbox.docker import DockerSandbox

    if prefer in ("auto", "docker"):
        docker = DockerSandbox()
        ok, reason = docker.available()
        if ok:
            return docker
        if prefer == "docker":
            raise SandboxUnavailable(f"Docker 沙箱不可用：{reason}")
    return LocalSandbox()
