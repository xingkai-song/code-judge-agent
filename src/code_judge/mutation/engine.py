"""变异体生成。

对一份参考解，逐个算子找出所有可变异的位置，每个位置产出一个**单点变异体**。

两个实现细节值得说明：

1. **按 ``ast.walk`` 的序号定位变异点**。第一遍找出所有候选节点并记下它在
   ``ast.walk`` 顺序里的下标；生成某个变异体时重新解析一遍源码、按下标取回同一个
   节点再改。这样每份变异体都是从**原始源码**出发只改一处，不会出现「叠加了
   多个变异」的脏样本。解析是确定性的，所以下标一定对得上。
2. **每个算子对每道题采样固定数量的位置**。位置太多会让变异体数量爆炸
   （一道题可能有十几个整型常量），采样能控制规模，同时保证可复现——
   随机种子由「算子 + 源码」决定，同样的输入永远得到同样的样本。
"""

from __future__ import annotations

import ast
import hashlib
import random
from dataclasses import dataclass

from code_judge.mutation.operators import OPERATORS, OperatorSpec, get_operator


@dataclass(frozen=True)
class Mutation:
    """一处在源码上的改动。"""

    operator: str
    label: str
    lineno: int
    before: str
    after: str

    def to_dict(self) -> dict:
        return {
            "operator": self.operator,
            "label": self.label,
            "lineno": self.lineno,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class Mutant:
    """一份只改了一处的错误代码。"""

    source: str
    mutation: Mutation
    site_index: int

    @property
    def fingerprint(self) -> str:
        return hashlib.sha1(self.source.encode("utf-8")).hexdigest()[:12]


def generate_mutants(
    source: str,
    operators: list[str] | None = None,
    max_per_operator: int = 2,
    seed: int = 0,
) -> list[Mutant]:
    """对一份源码生成变异体。

    :param operators: 只使用指定算子，None 表示全部
    :param max_per_operator: 每个算子最多在该题上取几个位置
    :param seed: 采样随机种子，保证可复现
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    specs = [get_operator(k) for k in operators] if operators else list(OPERATORS.values())
    nodes = list(ast.walk(tree))

    seen_sources: set[str] = set()
    mutants: list[Mutant] = []
    for spec in specs:
        sites = [idx for idx, node in enumerate(nodes) if _safe_match(spec, node)]
        sites = _sample(sites, max_per_operator, f"{seed}:{spec.key}", source)
        for idx in sites:
            mutant = _build_mutant(source, idx, spec)
            if mutant is None or mutant.source in seen_sources:
                continue
            seen_sources.add(mutant.source)
            mutants.append(mutant)
    return mutants


def _safe_match(spec: OperatorSpec, node: ast.AST) -> bool:
    try:
        return bool(spec.matcher(node))
    except Exception:
        return False


def _sample(sites: list[int], limit: int, salt: str, source: str) -> list[int]:
    if limit <= 0 or len(sites) <= limit:
        return sites
    rng = random.Random(f"{salt}:{hashlib.sha1(source.encode()).hexdigest()}")
    return sorted(rng.sample(sites, limit))


def _build_mutant(source: str, walk_index: int, spec: OperatorSpec) -> Mutant | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    nodes = list(ast.walk(tree))
    if walk_index >= len(nodes):
        return None
    target = nodes[walk_index]

    # before 必须在调用 mutator 之前算——mutator 是就地修改的
    before = _render_inline(target)
    try:
        replacement = spec.mutator(target)
    except Exception:
        return None
    if replacement is None:
        return None
    after = _render_inline(replacement)
    if before == after:
        return None

    ast.copy_location(replacement, target)
    ast.fix_missing_locations(replacement)

    class _SingleSite(ast.NodeTransformer):
        def visit(self, node):  # noqa: ANN001
            if node is target:
                return replacement
            return super().visit(node)

    new_tree = _SingleSite().visit(tree)
    ast.fix_missing_locations(new_tree)
    mutated = _render_source(new_tree)
    if not mutated or mutated == source:
        return None
    # 变异后必须仍是合法 Python，否则这条样本没用
    try:
        ast.parse(mutated)
    except SyntaxError:
        return None

    return Mutant(
        source=mutated,
        mutation=Mutation(
            operator=spec.key,
            label=spec.label,
            lineno=int(getattr(target, "lineno", 0)),
            before=before,
            after=after,
        ),
        site_index=walk_index,
    )


def _render_source(node: ast.AST) -> str:
    """渲染成**可执行**的源码，必须保留换行和缩进。"""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _render_inline(node: ast.AST) -> str:
    """渲染成**给人看的单行摘要**，用于报告里的 before / after。

    注意别把这两个函数搞混：早期版本只有 ``_unparse`` 一个函数，把换行压成空格
    后既拿去展示、又拿去当变异体源码，结果多语句函数被压成
    ``def f(n): a = 1 return a`` 这种非法代码，**所有变异体都在语法检查那一步被
    丢弃，一道题都产不出来**。
    """
    return " ".join(_render_source(node).split())
