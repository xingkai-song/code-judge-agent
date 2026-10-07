"""诊断结果的数据模型。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Repair:
    """一个通过了全部测试的单点修复——也就是说，这是一个被证明成立的改法。"""

    operator: str
    label: str
    lineno: int
    before: str
    after: str
    source: str
    verdict: str = "AC"
    passed: int = 0
    total: int = 0

    def to_dict(self) -> dict:
        return {
            "operator": self.operator,
            "label": self.label,
            "lineno": self.lineno,
            "before": self.before,
            "after": self.after,
            "source": self.source,
            "verdict": self.verdict,
            "passed": self.passed,
            "total": self.total,
        }


@dataclass
class Diagnosis:
    problem_id: str
    submitted_verdict: str
    passed: int = 0
    total: int = 0
    verified: bool = False
    operator: str = ""
    fault_label: str = ""
    lineno: int = 0
    repairs: list[Repair] = field(default_factory=list)
    candidates_tried: int = 0
    candidates_total: int = 0
    elapsed_ms: int = 0
    symptom: dict = field(default_factory=dict)
    explanation: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "problem_id": self.problem_id,
            "submitted_verdict": self.submitted_verdict,
            "passed": self.passed,
            "total": self.total,
            "verified": self.verified,
            "operator": self.operator,
            "fault_label": self.fault_label,
            "lineno": self.lineno,
            "repairs": [r.to_dict() for r in self.repairs],
            "candidates_tried": self.candidates_tried,
            "candidates_total": self.candidates_total,
            "elapsed_ms": self.elapsed_ms,
            "symptom": self.symptom,
            "explanation": self.explanation,
            "note": self.note,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
