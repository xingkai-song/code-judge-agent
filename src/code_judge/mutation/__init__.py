"""变异测试：从参考解自动生成「带错因标签」的数据。

思路来自软件工程里的变异测试（mutation testing）：**故意往正确代码里注入
已知类型的错误，注入的是什么错，标签就是什么错。**

这样一来，错因标签的 ground truth 不需要人工标注，也不需要任何人懂这道题——
标签是构造出来的，而「这个错误确实会导致失败」是判题机验证的。
"""

from code_judge.mutation.engine import Mutation, Mutant, generate_mutants
from code_judge.mutation.operators import OPERATORS, OperatorSpec, get_operator

__all__ = [
    "OPERATORS",
    "Mutation",
    "Mutant",
    "OperatorSpec",
    "generate_mutants",
    "get_operator",
]
