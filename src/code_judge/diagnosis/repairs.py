"""生成修复候选。

巧的地方在于：**复用变异算子**。这些算子绝大多数是自反的——

    边界条件：``<`` ↔ ``<=``
    比较符：  ``==`` ↔ ``!=``
    逻辑：    ``and`` ↔ ``or``
    参数顺序：交换两次回到原样
    差一：    +1 与 -1 互为逆

所以「往正确代码里注入错误」和「从错误代码里生成修复」是同一套算子。
在参考解上用它造训练数据，在学生的错误代码上用它找修复。

两个限制要如实说明：

* ``loop_control``（把 break 删掉）**不可逆**——从代码里恢复一个被删掉的 break
  需要知道该插在哪，这不是单点替换能做到的，所以它被排除在修复候选之外。
* 修复候选是有限的单点改动。真实学生的错误可能需要多处修改或结构性重写，
  这种情况闭环会明确返回「未定位」，而不是硬给一个结论。
"""

from __future__ import annotations

from dataclasses import dataclass

from code_judge.mutation.engine import generate_mutants

#: 不可逆的算子：不能从错误代码恢复，只能用来注入错误
IRREVERSIBLE_OPERATORS = {"loop_control"}

#: 修复候选的搜索顺序：先试最可能、最省事的改动。
#: 顺序会影响耗时（找到就停），不影响正确性——每个候选都会被实跑验证。
PREFERRED_ORDER = [
    "boundary_condition",
    "off_by_one_plus",
    "off_by_one_minus",
    "comparison_operator",
    "arithmetic_operator",
    "condition_negation",
    "condition_negation_drop",
    "boolean_operator",
    "initial_value",
    "return_value",
    "argument_order",
]


@dataclass(frozen=True)
class RepairCandidate:
    operator: str
    label: str
    lineno: int
    before: str
    after: str
    source: str


def propose_repairs(
    source: str,
    max_per_operator: int = 6,
    max_total: int = 48,
    exclude_operators: set[str] | None = None,
) -> list[RepairCandidate]:
    """为一份可能出错的代码生成修复候选，按搜索顺序排列。"""
    excluded = set(exclude_operators or ())
    operators = [
        k for k in PREFERRED_ORDER
        if k not in IRREVERSIBLE_OPERATORS and k not in excluded
    ]
    mutants = generate_mutants(source, operators=operators, max_per_operator=max_per_operator)

    seen: set[str] = set()
    candidates: list[RepairCandidate] = []
    for mutant in mutants:
        if mutant.source in seen:
            continue
        seen.add(mutant.source)
        candidates.append(
            RepairCandidate(
                operator=mutant.mutation.operator,
                label=mutant.mutation.label,
                lineno=mutant.mutation.lineno,
                before=mutant.mutation.before,
                after=mutant.mutation.after,
                source=mutant.source,
            )
        )
    # 按算子的偏好顺序稳定排序：generate_mutants 是按算子分组产出的，
    # 这里保持分组顺序即可，组内按行号先后
    rank = {k: i for i, k in enumerate(PREFERRED_ORDER)}
    candidates.sort(key=lambda c: (rank.get(c.operator, 99), c.lineno))
    return candidates[:max_total]
