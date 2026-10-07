"""诊断闭环：判题 → 定位错因 → 生成讲解 → 验证讲解。

核心设计是**把诊断变成一个可验证的搜索**：

1. 先判题，拿到失败用例和具体症状（期望值、实际值、异常类型）。
2. 对失败代码枚举**单点修复候选**（复用变异算子——算子大多是自反的，
   对错误代码施加一次算子就得到修复候选）。
3. 每个候选都交给判题机实跑。**能通过全部用例的候选就是一个被证明的修复**，
   它同时给出错因类型和出错位置。
4. 讲解由这些证据生成，而不是由模型自由发挥——找不到可验证修复时明确说
   「未定位」，而不是编一个理由。

这样做的价值在于：错因结论不是「猜的」，而是「改一处、跑一遍、过了」。
"""

from code_judge.diagnosis.diagnoser import Diagnoser
from code_judge.diagnosis.explainer import explain
from code_judge.diagnosis.models import Diagnosis, Repair
from code_judge.diagnosis.repairs import propose_repairs

__all__ = ["Diagnoser", "Diagnosis", "Repair", "explain", "propose_repairs"]
