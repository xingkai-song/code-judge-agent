"""变异算子定义。

每个算子回答两个问题：**在哪里改**（matcher）和**怎么改**（mutator）。
算子只做「单点变异」——一次只改一处，这样错因标签才是干净的：
如果一次改好几处，就没法说清失败到底是哪一处造成的。

算子覆盖的是编程初学者最常犯的几类错误，和真实的错因分类是对应的。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class OperatorSpec:
    key: str
    label: str
    description: str
    matcher: Callable[[ast.AST], bool]
    mutator: Callable[[ast.AST], ast.AST | None]


# --------------------------------------------------------------------------- #
# 各类节点的匹配辅助
# --------------------------------------------------------------------------- #

_CMP_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


def _is_true_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)


def _is_int_constant(node: ast.AST) -> bool:
    # 注意排除 bool：Python 里 True 也是 int，误判会生成无意义的变异
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(
        node.value, bool
    )


# --------------------------------------------------------------------------- #
# 算子实现
# --------------------------------------------------------------------------- #


def _boundary_matcher(node: ast.AST) -> bool:
    return isinstance(node, ast.Compare) and any(
        isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in node.ops
    )


def _flip_boundary(node: ast.Compare) -> ast.AST:
    """严格与不严格互换：``<`` ↔ ``<=``，``>`` ↔ ``>=``。"""
    swap = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt}
    new_ops = [swap[type(op)]() if type(op) in swap else op for op in node.ops]
    node.ops = new_ops
    return node


def _comparison_matcher(node: ast.AST) -> bool:
    return isinstance(node, ast.Compare) and any(isinstance(op, _CMP_OPS) for op in node.ops)


def _swap_comparison(node: ast.Compare) -> ast.AST:
    """关系互换：``==`` ↔ ``!=``，``<`` ↔ ``>``。"""
    swap = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.Gt, ast.Gt: ast.Lt}
    node.ops = [swap[type(op)]() if type(op) in swap else op for op in node.ops]
    return node


def _arithmetic_matcher(node: ast.AST) -> bool:
    return isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
    )


def _swap_arithmetic(node: ast.BinOp) -> ast.AST:
    """算术运算符替换。除法相关只是「换一种除」，避免直接把结果变成非数值。"""
    table: dict[type, ast.operator] = {
        ast.Add: ast.Sub(),
        ast.Sub: ast.Add(),
        ast.Mult: ast.FloorDiv(),
        ast.FloorDiv: ast.Mult(),
        ast.Div: ast.FloorDiv(),
        ast.Mod: ast.FloorDiv(),
    }
    node.op = table[type(node.op)]
    return node


def _off_by_one_matcher(node: ast.AST) -> bool:
    return _is_int_constant(node)


def _off_by_one_plus(node: ast.Constant) -> ast.AST:
    return ast.Constant(value=node.value + 1)


def _off_by_one_minus(node: ast.Constant) -> ast.AST:
    return ast.Constant(value=node.value - 1)


def _boolean_matcher(node: ast.AST) -> bool:
    return isinstance(node, ast.BoolOp)


def _swap_boolean(node: ast.BoolOp) -> ast.AST:
    node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    return node


def _condition_matcher(node: ast.AST) -> bool:
    if not isinstance(node, (ast.If, ast.While)):
        return False
    return not isinstance(node.test, ast.UnaryOp) or not isinstance(node.test.op, ast.Not)


def _negate_condition(node: ast.If | ast.While) -> ast.AST:
    node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
    return node


def _initializer_matcher(node: ast.AST) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    return _is_int_constant(node.value) and node.value.value in (0, 1)


def _flip_initializer(node: ast.Assign) -> ast.AST:
    node.value = ast.Constant(value=1 if node.value.value == 0 else 0)
    return node


def _args_matcher(node: ast.AST) -> bool:
    # 常量对调基本无意义（f(1, 2)），跳过可以少产生一堆等价变异
    return isinstance(node, ast.Call) and len(node.args) >= 2 and not all(
        isinstance(a, ast.Constant) for a in node.args[:2]
    )


def _swap_args(node: ast.Call) -> ast.AST:
    node.args[0], node.args[1] = node.args[1], node.args[0]
    return node


def _loop_control_matcher(node: ast.AST) -> bool:
    return isinstance(node, (ast.Break, ast.Continue))


def _drop_loop_control(node: ast.Break | ast.Continue) -> ast.AST:
    return ast.Pass()


def _return_matcher(node: ast.AST) -> bool:
    if not isinstance(node, ast.Return) or node.value is None:
        return False
    return _is_true_constant(node.value) or _is_int_constant(node.value)


def _flip_return(node: ast.Return) -> ast.AST:
    value = node.value
    if isinstance(value, ast.Constant) and isinstance(value.value, bool):
        node.value = ast.Constant(value=not value.value)
    else:
        node.value = ast.Constant(value=value.value + 1)
    return node


# --------------------------------------------------------------------------- #
# 算子注册表
# --------------------------------------------------------------------------- #

OPERATORS: dict[str, OperatorSpec] = {
    spec.key: spec
    for spec in (
        OperatorSpec(
            key="boundary_condition",
            label="边界条件错误",
            description="严格与不严格比较互换，例如 i < n 写成 i <= n",
            matcher=_boundary_matcher,
            mutator=_flip_boundary,
        ),
        OperatorSpec(
            key="comparison_operator",
            label="比较符误用",
            description="关系互换，例如 == 写成 !=、< 写成 >",
            matcher=_comparison_matcher,
            mutator=_swap_comparison,
        ),
        OperatorSpec(
            key="arithmetic_operator",
            label="算术运算符误用",
            description="算符替换，例如 + 写成 -、* 写成 //",
            matcher=_arithmetic_matcher,
            mutator=_swap_arithmetic,
        ),
        OperatorSpec(
            key="off_by_one_plus",
            label="差一错误（偏大）",
            description="整型常量加一，模拟多算一次",
            matcher=_off_by_one_matcher,
            mutator=_off_by_one_plus,
        ),
        OperatorSpec(
            key="off_by_one_minus",
            label="差一错误（偏小）",
            description="整型常量减一，模拟少算一次",
            matcher=_off_by_one_matcher,
            mutator=_off_by_one_minus,
        ),
        OperatorSpec(
            key="boolean_operator",
            label="逻辑运算符误用",
            description="and 与 or 互换",
            matcher=_boolean_matcher,
            mutator=_swap_boolean,
        ),
        OperatorSpec(
            key="condition_negation",
            label="条件判断取反",
            description="if / while 的条件整体取反",
            matcher=_condition_matcher,
            mutator=_negate_condition,
        ),
        OperatorSpec(
            key="initial_value",
            label="初始值错误",
            description="累加器初值在 0 与 1 之间翻转",
            matcher=_initializer_matcher,
            mutator=_flip_initializer,
        ),
        OperatorSpec(
            key="argument_order",
            label="参数顺序错误",
            description="函数调用的前两个实参对调",
            matcher=_args_matcher,
            mutator=_swap_args,
        ),
        OperatorSpec(
            key="loop_control",
            label="循环控制错误",
            description="break / continue 被去掉",
            matcher=_loop_control_matcher,
            mutator=_drop_loop_control,
        ),
        OperatorSpec(
            key="return_value",
            label="返回值错误",
            description="返回的布尔值取反或整数偏移",
            matcher=_return_matcher,
            mutator=_flip_return,
        ),
    )
}


def get_operator(key: str) -> OperatorSpec:
    try:
        return OPERATORS[key]
    except KeyError:
        raise KeyError(f"未知变异算子 {key!r}，可用：{sorted(OPERATORS)}") from None
