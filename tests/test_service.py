"""Web 服务层的接口测试。

这些测试跑在真实题库上（加载一次，模块级复用），验证的是「服务把核心逻辑
接对了没有」：参数校验、404、以及最关键的「提交 → 轮询 → 拿到诊断」这条链路。

题库内容会变，所以测试不写死具体题目 id，而是挑一道最轻的题来跑。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: 变异数据集不入库（76MB，可由 scripts/mutate.py 生成），没有它服务起不来
pytestmark = pytest.mark.skipif(
    not (ROOT / "eval" / "datasets" / "mutants.jsonl").exists(),
    reason="缺少变异数据集，先跑 scripts/mutate.py",
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from code_judge.service import create_app

    workspaces = tmp_path_factory.mktemp("web_workspaces")
    app = create_app(root=ROOT, sandbox="local", workspaces=workspaces, workers=1)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def sample(client: TestClient):
    """挑一道用例最少的题 + 它的参考解，让端到端测试跑得快。"""
    from code_judge.problems import load_problems_jsonl
    from code_judge.references import load_references

    problems = load_problems_jsonl(ROOT / "problems" / "codecontests.jsonl")
    references = load_references(ROOT / "solutions" / "codecontests.jsonl")
    problem = min(problems, key=lambda p: (len(p.cases), len(p.statement)))
    return problem, references[problem.id].source


def _poll(client: TestClient, task_id: str, timeout_s: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        payload = client.get(f"/api/task/{task_id}").json()
        if payload["status"] in ("done", "error"):
            return payload
        time.sleep(0.2)
    raise AssertionError("判题任务超时未完成")


# --------------------------------------------------------------------------- #
# 基础接口
# --------------------------------------------------------------------------- #


def test_health_reports_library_and_sandbox(client: TestClient):
    body = client.get("/api/health").json()
    assert body["problems"] > 100
    assert body["skills"] > 0
    assert body["sandbox"] == "local"


def test_recommendation_returns_ranked_problems(client: TestClient):
    body = client.get("/api/recommend", params={"user": "u_rec", "limit": 3}).json()
    recs = body["recommendations"]
    assert recs, "新用户也应该拿到推荐"
    for rec in recs:
        assert rec["problem_id"]
        assert rec["target_label"]
        assert rec["reason"]


def test_problem_detail_includes_statement_and_cases(client: TestClient, sample):
    problem, _ = sample
    body = client.get(f"/api/problem/{problem.id}", params={"user": "u_prob"}).json()
    assert body["kind"] == "stdio"
    assert body["statement"]
    assert body["case_count"] >= 1
    # 标准输入输出型的题没有函数签名，起步代码交给前端填骨架
    assert body["starter_code"] == ""


def test_unknown_problem_returns_404(client: TestClient):
    response = client.get("/api/problem/NOT_A_REAL_PROBLEM", params={"user": "u_404"})
    assert response.status_code == 404


def test_task_not_found_returns_404(client: TestClient):
    assert client.get("/api/task/deadbeef").status_code == 404


# --------------------------------------------------------------------------- #
# 参数校验：用户标识会拼进文件路径，必须挡住穿越类输入
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", "名字", "x" * 40])
def test_invalid_user_is_rejected(client: TestClient, bad: str):
    response = client.get("/api/recommend", params={"user": bad})
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# 端到端：提交 → 轮询 → 结果
# --------------------------------------------------------------------------- #


def test_reference_solution_is_accepted(client: TestClient, sample):
    """题库自检的服务层版本：参考解必须能通过。"""
    problem, reference = sample
    response = client.post(
        "/api/submit",
        json={"user": "u_ac", "problem_id": problem.id, "source": reference},
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    task = _poll(client, task_id)
    assert task["status"] == "done", task
    result = task["result"]
    assert result["accepted"], result["explanation"]
    assert result["verdict"] == "AC"
    assert result["diagnosis"] is None, "通过的提交不该有诊断"


def test_wrong_submission_returns_diagnosis(client: TestClient, sample):
    problem, _ = sample
    response = client.post(
        "/api/submit",
        json={
            "user": "u_submit",
            "problem_id": problem.id,
            "source": "print(0)\n",
        },
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    task = _poll(client, task_id)
    assert task["status"] == "done", task
    result = task["result"]
    assert result["verdict"] != "AC"
    assert not result["accepted"]
    # 失败时必须给出讲解（哪怕是「未定位」也要说明线索，不能是空字符串）
    assert result["explanation"].strip()
    assert result["diagnosis"] is not None


def test_successful_submission_updates_profile(client: TestClient, sample):
    problem, reference = sample
    client.post(
        "/api/submit",
        json={"user": "u_profile", "problem_id": problem.id, "source": reference},
    )
    # 轮询到最后一条任务
    for _ in range(200):
        profile = client.get("/api/profile", params={"user": "u_profile"}).json()
        if profile["attempts"]:
            break
        time.sleep(0.2)

    assert profile["attempts"] == 1
    assert profile["solved"] == 1
    assert profile["history"][0]["verdict"] == "AC"


def test_submit_to_unknown_problem_is_404(client: TestClient):
    response = client.post(
        "/api/submit",
        json={"user": "u_bad", "problem_id": "NOPE", "source": "x = 1\n"},
    )
    assert response.status_code == 404
