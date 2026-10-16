"""后台判题任务。

判题是阻塞操作（要起子进程，失败时还要试几十个修复候选），不能让 HTTP 请求
干等，所以拆成两步：**提交拿任务号 → 前端轮询结果**。

任务表放在进程内存里，只保留最近若干条，进程重启即清空。单人小范围使用
够用；真要持久化或多实例部署，应该换成外部队列加数据库。
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from code_judge.tutoring.session import SubmissionOutcome
from code_judge.service.runtime import ServiceRuntime


@dataclass
class TaskState:
    """一次提交的进度与结果。"""

    id: str
    user: str
    problem_id: str
    status: str = "queued"  # queued / running / done / error
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: dict | None = None
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.id,
            "user": self.user,
            "problem_id": self.problem_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }


def serialize_outcome(outcome: SubmissionOutcome) -> dict:
    """把一次提交的结果整理成前端要用的形状。

    刻意**不返回修复后的完整源码**：诊断给出的是「哪一行、由什么改成什么」，
    学生照着改一遍比直接抄答案更有价值。
    """
    payload: dict = {
        "problem_id": outcome.problem_id,
        "verdict": outcome.verdict,
        "passed": outcome.passed,
        "total": outcome.total,
        "accepted": outcome.accepted,
        "explanation": outcome.explanation,
        "diagnosis": None,
        "recommendations": [r.to_dict() for r in outcome.next_recommendations],
    }
    diagnosis = outcome.diagnosis
    if diagnosis is not None:
        repair = diagnosis.repairs[0] if diagnosis.repairs else None
        payload["diagnosis"] = {
            "verified": diagnosis.verified,
            "fault_label": diagnosis.fault_label,
            "operator": diagnosis.operator,
            "lineno": diagnosis.lineno,
            "note": diagnosis.note,
            "elapsed_ms": diagnosis.elapsed_ms,
            "candidates_tried": diagnosis.candidates_tried,
            "candidates_total": diagnosis.candidates_total,
            "symptom": diagnosis.symptom,
            "repair": (
                {
                    "lineno": repair.lineno,
                    "before": repair.before,
                    "after": repair.after,
                    "label": repair.label,
                    "passed": repair.passed,
                    "total": repair.total,
                }
                if repair
                else None
            ),
            "alternative_repairs": max(0, len(diagnosis.repairs) - 1),
        }
    return payload


class TaskQueue:
    def __init__(self, runtime: ServiceRuntime, workers: int = 2, keep: int = 200) -> None:
        self.runtime = runtime
        self.workers = max(1, workers)
        self.keep = max(10, keep)
        self._executor = ThreadPoolExecutor(
            max_workers=self.workers, thread_name_prefix="judge"
        )
        self._tasks: dict[str, TaskState] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    # -- 对外 ---------------------------------------------------------------- #

    def submit(self, user: str, problem_id: str, source: str) -> TaskState:
        task = TaskState(id=uuid.uuid4().hex[:16], user=user, problem_id=problem_id)
        with self._lock:
            self._tasks[task.id] = task
            self._order.append(task.id)
            self._evict_locked()
        self._executor.submit(self._run, task, source)
        return task

    def get(self, task_id: str) -> TaskState | None:
        with self._lock:
            return self._tasks.get(task_id)

    def stats(self) -> dict:
        with self._lock:
            pending = sum(1 for t in self._tasks.values() if t.status in ("queued", "running"))
            return {"workers": self.workers, "tracked": len(self._tasks), "pending": pending}

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- 内部 ---------------------------------------------------------------- #

    def _run(self, task: TaskState, source: str) -> None:
        self._set_status(task, "running")
        try:
            # 同一个用户串行（TutorSession 有状态），不同用户之间并行
            with self.runtime.lock(task.user):
                session = self.runtime.session(task.user)
                outcome = session.submit(source, task.problem_id)
                payload = serialize_outcome(outcome)
            with self._lock:
                task.result = payload
                task.status = "done"
                task.updated_at = time.time()
        except Exception as exc:  # 判题机的异常不能把服务带崩
            with self._lock:
                task.status = "error"
                task.error = f"{type(exc).__name__}: {exc}"
                task.updated_at = time.time()

    def _set_status(self, task: TaskState, status: str) -> None:
        with self._lock:
            task.status = status
            task.updated_at = time.time()

    def _evict_locked(self) -> None:
        while len(self._order) > self.keep:
            oldest = self._order.pop(0)
            current = self._tasks.get(oldest)
            # 正在跑的任务不删，否则前端轮询会突然 404
            if current is not None and current.status in ("queued", "running"):
                self._order.append(oldest)
                break
            self._tasks.pop(oldest, None)
