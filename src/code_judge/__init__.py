"""code_judge：以判题沙箱为 ground truth 的编程题诊断 Agent。

当前阶段（Day 1–2）交付判题核心：

    types      —— 判定结果的数据模型（Verdict / TestCase / Problem / RunReport）
    compare    —— 输出比对（精确 / 分词 / 浮点容差）
    languages  —— 语言注册表（源码文件名、编译命令、运行命令）
    sandbox    —— 执行沙箱抽象，LocalSandbox 与 DockerSandbox 两种实现
    verifier   —— 判题器，把上面几层组装成一次完整判定
    problems   —— 题目文件的读写
"""

from code_judge.types import (
    CaseResult,
    Checker,
    Problem,
    ProblemKind,
    RunReport,
    TestCase,
    Verdict,
)
from code_judge.verifier import CodeVerifier

__all__ = [
    "CaseResult",
    "Checker",
    "CodeVerifier",
    "Problem",
    "ProblemKind",
    "RunReport",
    "TestCase",
    "Verdict",
]

__version__ = "0.1.0"
