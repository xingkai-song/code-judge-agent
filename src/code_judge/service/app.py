"""FastAPI 应用：把核心库暴露成浏览器可调用的接口。

接口一共七个，全部围绕「推荐 → 看题 → 提交 → 看诊断 → 看档案」这条链路：

    GET  /                      单页前端
    GET  /api/health            服务与沙箱状态
    GET  /api/skills            技能图谱（有哪些技能、各有多少题可练）
    GET  /api/recommend         推荐题目
    GET  /api/problem/{id}      题面 + 起步代码
    POST /api/submit            提交代码，立刻返回任务号
    GET  /api/task/{id}         查询判题进度/结果（前端轮询）
    GET  /api/profile           掌握度档案

没有任何鉴权：这是**单机自用**的服务，用户标识只是一个用来分档案的名字。
要开放给多人使用，必须先补上登录、限流和容器隔离（见 README 的说明）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from code_judge.service.runtime import InvalidUser, ServiceRuntime, normalize_user
from code_judge.service.tasks import TaskQueue

STATIC_DIR = Path(__file__).parent / "static"


class SubmitRequest(BaseModel):
    user: str
    problem_id: str
    source: str = Field(min_length=1, max_length=200_000)


def create_app(
    root: str | Path,
    sandbox: str = "local",
    workspaces: str | Path | None = None,
    workers: int = 2,
) -> FastAPI:
    runtime = ServiceRuntime(root=root, sandbox_prefer=sandbox, workspaces_dir=workspaces)
    queue = TaskQueue(runtime, workers=workers)

    app = FastAPI(
        title="编程题诊断与陪练",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.runtime = runtime
    app.state.queue = queue
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def check_user(raw: str | None) -> str:
        try:
            return normalize_user(raw)
        except InvalidUser as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # -- 页面与状态 ---------------------------------------------------------- #

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict:
        return {**runtime.info(), **queue.stats()}

    @app.get("/api/skills")
    def skills() -> dict:
        return {"skills": runtime.skills}

    # -- 推荐与题面 ---------------------------------------------------------- #

    @app.get("/api/recommend")
    def recommend(
        user: str = Query(...), limit: int = Query(3, ge=1, le=10)
    ) -> dict:
        who = check_user(user)
        with runtime.lock(who):
            session = runtime.session(who)
            recommendations = session.recommend(limit=limit)
        return {"user": who, "recommendations": [r.to_dict() for r in recommendations]}

    @app.get("/api/problem/{problem_id}")
    def problem(problem_id: str, user: str = Query(...)) -> dict:
        who = check_user(user)
        target = runtime.problems.get(problem_id)
        if target is None:
            raise HTTPException(status_code=404, detail=f"题库里没有 {problem_id}")
        with runtime.lock(who):
            session = runtime.session(who)
            starter = session.starter_code(target)
            attempted = problem_id in session.profile.attempted_problem_ids

        skill_keys = sorted(runtime.problem_skills.get(problem_id, ()))
        return {
            "id": target.id,
            "title": target.title,
            "statement": target.statement,
            "kind": target.kind.value,
            "entry_point": target.entry_point,
            "difficulty": target.difficulty,
            "source": target.source,
            "time_limit_ms": target.time_limit_ms,
            "memory_limit_mb": target.memory_limit_mb,
            "case_count": len(target.cases),
            "starter_code": starter,
            "attempted": attempted,
            "skills": [
                {"key": key, "label": runtime.labels.get(key, key)} for key in skill_keys
            ],
        }

    # -- 提交与判题 ---------------------------------------------------------- #

    @app.post("/api/submit")
    def submit(request: SubmitRequest) -> dict:
        who = check_user(request.user)
        if request.problem_id not in runtime.problems:
            raise HTTPException(status_code=404, detail=f"题库里没有 {request.problem_id}")
        task = queue.submit(who, request.problem_id, request.source)
        return {"task_id": task.id, "status": task.status}

    @app.get("/api/task/{task_id}")
    def task(task_id: str) -> dict:
        state = queue.get(task_id)
        if state is None:
            raise HTTPException(status_code=404, detail="任务不存在或已过期")
        return state.to_dict()

    # -- 档案 ---------------------------------------------------------------- #

    @app.get("/api/profile")
    def profile(user: str = Query(...)) -> dict:
        who = check_user(user)
        with runtime.lock(who):
            session = runtime.session(who)
            attempts = list(session.profile.attempts)
            snapshot = session.mastery_snapshot()
            weakest = session.weakest_with_evidence()

        errors: dict[str, int] = {}
        for attempt in attempts:
            if attempt.fault_operator:
                errors[attempt.fault_operator] = errors.get(attempt.fault_operator, 0) + 1

        return {
            "user": who,
            "attempts": len(attempts),
            "solved": sum(1 for a in attempts if a.solved),
            "skills": [
                {
                    "key": item.skill,
                    "label": item.label,
                    "mastery": round(item.mean, 4),
                    "uncertainty": round(item.std, 4),
                    "opportunities": item.opportunities,
                    "errors": item.errors,
                    "level": item.level,
                }
                for item in snapshot
            ],
            "error_counts": [
                {"key": key, "label": runtime.labels.get(key, key), "count": count}
                for key, count in sorted(errors.items(), key=lambda kv: -kv[1])
            ],
            "weakest": (
                {
                    "key": weakest.skill,
                    "label": weakest.label,
                    "mastery": round(weakest.mean, 4),
                    "opportunities": weakest.opportunities,
                }
                if weakest
                else None
            ),
            "history": [
                {
                    "problem_id": a.problem_id,
                    "verdict": a.verdict,
                    "passed": a.passed,
                    "total": a.total,
                    "fault_label": a.fault_label,
                    "ts": a.ts,
                }
                for a in reversed(attempts[-30:])
            ],
        }

    return app
