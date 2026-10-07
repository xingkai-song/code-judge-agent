"""从变异数据集推导「题目 → 考察哪些技能」。

不用人工给题目打技能标签——变异测试已经把这个信息生产出来了：
某道题能检出 k 类错误（存在该类型的有效变异体），说明这道题的测试用例
**确实能考察 k 这项技能**。

这算是变异数据集的一个复用：同一份数据既用来做错因标签，又用来做技能标注。
"""

from __future__ import annotations

from pathlib import Path

from code_judge.mutation.dataset import load_mutants


def load_problem_skills(path: str | Path) -> tuple[dict[str, set[str]], dict[str, str]]:
    """返回 (题目 → 技能集合, 技能 → 中文标签)。

    技能用变异算子的 key 表示；标签直接取数据集里记录的错因名，
    保证「技能名」和「诊断输出的错因名」是同一套词表——学生看到的用词前后一致。
    """
    problem_skills: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    for mutant in load_mutants(path):
        problem_skills.setdefault(mutant.problem_id, set()).add(mutant.operator)
        labels.setdefault(mutant.operator, mutant.error_label)
    return problem_skills, labels


def skill_summary(problem_skills: dict[str, set[str]], labels: dict[str, str]) -> list[dict]:
    counts: dict[str, int] = {}
    for skills in problem_skills.values():
        for skill in skills:
            counts[skill] = counts.get(skill, 0) + 1
    return [
        {"skill": skill, "label": labels.get(skill, skill), "problems": n}
        for skill, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
