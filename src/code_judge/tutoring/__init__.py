"""掌握度建模与针对性出题。

把「单次诊断」变成「持续陪练」需要三步：

1. **技能映射**：知道每道题能考察哪些技能。这里不用人工打标签，而是从变异数据集
   自动推导——某道题能检出 k 类错误，说明这道题就考察 k 这项技能。
2. **掌握度估计**：用 Beta-Binomial 后验估计学生对每项技能的掌握程度。
   选择这个模型是有意的：它自带**不确定度**，而「不确定度」正是决定
   「该练习还是该补测」的依据。
3. **选题**：挑「掌握度下界最低」的技能，再从中选一道最能聚焦该技能的题。

用下界而不是均值来排序，等于自带探索——练得少的技能不确定度高，下界被压低，
系统会主动去补测，而不是反复在已知的弱项上打转。
"""

from code_judge.tutoring.mastery import MasteryModel, SkillMastery
from code_judge.tutoring.models import Attempt, LearnerProfile, Recommendation
from code_judge.tutoring.recommender import Recommender
from code_judge.tutoring.skills import load_problem_skills

__all__ = [
    "Attempt",
    "LearnerProfile",
    "MasteryModel",
    "Recommendation",
    "Recommender",
    "SkillMastery",
    "load_problem_skills",
]
