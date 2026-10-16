"""服务运行时：题库、技能图谱、沙箱和各用户会话的容器。

进程启动时把这些加载一次（题库 591 道 + 技能图谱约 3 秒），之后所有请求共用，
避免每个请求都重新解析 JSONL。

两个刻意的设计：

* **每个用户一把锁**。``TutorSession`` 内部有状态（档案、推荐计数），
  同一个用户并发提交会让掌握度互相覆盖，所以同一用户串行、不同用户并行。
* **用户标识先校验再拼路径**。它会拼进 ``workspace/<user>/profile.json``，
  必须挡住 ``../`` 这类输入——服务再小也不能把文件写到工作区外面去。
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from code_judge.diagnosis import Diagnoser
from code_judge.problems import load_library
from code_judge.sandbox import default_sandbox
from code_judge.tutoring.session import TutorSession
from code_judge.tutoring.skills import filter_tutorable, load_problem_skills, skill_summary
from code_judge.verifier import CodeVerifier

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class InvalidUser(ValueError):
    """用户标识不合法。"""


def normalize_user(raw: str | None) -> str:
    """校验并归一化用户标识。"""
    user = (raw or "").strip()
    if not USER_ID_PATTERN.match(user):
        raise InvalidUser("用户标识只能用字母、数字、下划线、短横线，长度 1–32")
    return user


class ServiceRuntime:
    def __init__(
        self,
        root: str | Path,
        sandbox_prefer: str = "local",
        workspaces_dir: str | Path | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.workspaces = Path(workspaces_dir) if workspaces_dir else self.root / "workspace"
        self.workspaces.mkdir(parents=True, exist_ok=True)

        mutants = self.root / "eval" / "datasets" / "mutants.jsonl"
        if not mutants.exists():
            raise FileNotFoundError(
                f"找不到变异数据集 {mutants}，请先运行 scripts/mutate.py --jobs 6"
            )

        self.sandbox = default_sandbox(sandbox_prefer)
        self.verifier = CodeVerifier(sandbox=self.sandbox)
        self.problems = load_library(self.root / "problems")

        problem_skills, labels = load_problem_skills(mutants)
        # 不能诊断的技能不出题；只保留题库里真实存在的题目
        problem_skills, labels = filter_tutorable(problem_skills, labels)
        self.problem_skills = {
            pid: skills for pid, skills in problem_skills.items() if pid in self.problems
        }
        self.labels = labels
        self.skills = skill_summary(self.problem_skills, labels)

        self._sessions: dict[str, TutorSession] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- 会话 ---------------------------------------------------------------- #

    def session(self, user_id: str | None) -> TutorSession:
        """取（或新建）某个用户的陪练会话。调用方应先持有该用户的锁。"""
        user = normalize_user(user_id)
        with self._guard:
            session = self._sessions.get(user)
            if session is None:
                session = TutorSession(
                    user_id=user,
                    problems=self.problems,
                    problem_skills=self.problem_skills,
                    labels=self.labels,
                    verifier=self.verifier,
                    diagnoser=Diagnoser(verifier=self.verifier, max_repairs=2),
                    profile_path=self.workspaces / user / "profile.json",
                )
                self._sessions[user] = session
            return session

    def lock(self, user_id: str | None) -> threading.Lock:
        user = normalize_user(user_id)
        with self._guard:
            lock = self._locks.get(user)
            if lock is None:
                lock = threading.Lock()
                self._locks[user] = lock
            return lock

    # -- 展示 ---------------------------------------------------------------- #

    def info(self) -> dict:
        ok, reason = self.sandbox.available()
        return {
            "problems": len(self.problems),
            "skills": len(self.labels),
            "labeled_problems": len(self.problem_skills),
            "sandbox": self.sandbox.name,
            "sandbox_available": ok,
            "sandbox_reason": reason,
            "sandbox_enforces_memory": self.sandbox.enforces_memory,
            "sandbox_enforces_network_isolation": self.sandbox.enforces_network_isolation,
            "loaded_users": len(self._sessions),
        }
