"""语言注册表。

每种语言需要回答四个问题：源码文件叫什么、要不要编译、编译命令是什么、
运行命令是什么。Docker 沙箱还需要知道用哪个镜像。

加一门新语言只需要在 LANGUAGES 里补一条记录，判题器不用改。
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    key: str
    display: str
    source_name: str
    run_cmd: tuple[str, ...]
    compile_cmd: tuple[str, ...] | None = None
    compile_output: str = ""
    required_tools: tuple[str, ...] = ()
    docker_image: str = ""
    version_cmd: tuple[str, ...] | None = None

    @property
    def needs_compile(self) -> bool:
        return self.compile_cmd is not None


def _python_cmd() -> str:
    """判断用的解释器：优先 PATH 上的 python3，退回当前解释器。"""
    return shutil.which("python3") or sys.executable


LANGUAGES: dict[str, Language] = {
    "python": Language(
        key="python",
        display="Python 3",
        source_name="main.py",
        run_cmd=("python3", "main.py"),
        required_tools=("python3",),
        docker_image="python:3.11-slim",
        version_cmd=("python3", "--version"),
    ),
    "cpp": Language(
        key="cpp",
        display="C++17",
        source_name="main.cpp",
        compile_cmd=("c++", "-O2", "-std=c++17", "-o", "main", "main.cpp"),
        compile_output="main",
        run_cmd=("./main",),
        required_tools=("c++",),
        docker_image="gcc:13",
        version_cmd=("c++", "--version"),
    ),
}


def get_language(key: str) -> Language:
    try:
        return LANGUAGES[key]
    except KeyError:
        known = ", ".join(sorted(LANGUAGES))
        raise KeyError(f"未知语言 {key!r}，已支持：{known}") from None


def available_languages() -> list[str]:
    """当前机器上真正能跑的语言。"""
    return [k for k, lang in LANGUAGES.items() if _tools_ok(lang)]


def _tools_ok(lang: Language) -> bool:
    if lang.key == "python":
        return bool(shutil.which("python3") or sys.executable)
    return all(shutil.which(t) for t in lang.required_tools)
