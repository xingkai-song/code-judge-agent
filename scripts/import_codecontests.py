#!/usr/bin/env python3
"""导入 CodeContests（DeepMind）作为题库。

数据来自 HuggingFace 的 parquet 镜像（官方原件是 3GiB 的 Riegeli protobuf，
需要 gsutil + bazel 才能读；parquet 镜像可以直接用 pyarrow 打开）。

用法：
    python scripts/import_codecontests.py                      # 测试集 165 题
    python scripts/import_codecontests.py --train-shards 4     # 再取 4 个训练分片
    python scripts/import_codecontests.py --max-cases 10       # 每题保留几条用例

产出：
    problems/codecontests.jsonl    题库（标准输入输出型，不含答案）
    solutions/codecontests.jsonl   参考解（只有 Python3 的题才会进来）
    data/raw/codecontests/*.parquet  原始分片，保留以便离线重跑
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_judge.datasets import CcEntry, convert_codecontests_entries  # noqa: E402
from code_judge.problems import save_problems_jsonl  # noqa: E402
from code_judge.references import save_references  # noqa: E402
from code_judge.references import ReferenceSolution  # noqa: E402
from code_judge.sandbox import default_sandbox  # noqa: E402
from code_judge.types import Verdict  # noqa: E402
from code_judge.verifier import CodeVerifier  # noqa: E402

REPO = "https://huggingface.co/datasets/deepmind/code_contests/resolve/main/data"
API = "https://huggingface.co/api/datasets/deepmind/code_contests"
TEST_SHARD = "test-00000-of-00001-9c49eeff30aacaa8.parquet"


def list_remote_train_shards(retries: int = 5) -> list[str]:
    """从数据集仓库动态列出训练分片（分片数量和文件名里的哈希会变，不写死）。

    国内访问 HuggingFace 会偶发 SSL 中断，所以这里带重试。
    """
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            request = urllib.request.Request(
                API, headers={"User-Agent": "code-judge-agent/0.1"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read())
            names = sorted(
                name
                for name in (s.get("rfilename", "") for s in payload.get("siblings", []))
                if "/train-" in name
            )
            return [name.split("/")[-1] for name in names]
        except Exception as exc:  # 网络抖动，重试
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"无法获取分片列表：{last_error}")


def download_shard(filename: str, target_dir: Path, attempts: int = 8) -> Path:
    """下载分片：断点续传 + 完整性校验 + 失败重试。

    这套数据集单个分片有 60–180MB，网络一断就会留下一个「大小不对」的残file；
    所以下完必须核对大小，不对就接着传，而不是直接拿去解析。
    """
    target = target_dir / filename
    url = f"{REPO}/{filename}"
    # 先问大小，用来判断本地文件是否完整
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)

    for attempt in range(1, attempts + 1):
        done = target.stat().st_size if target.exists() else 0
        if total and done == total:
            print(f"  已完整下载，跳过：{filename}")
            return target
        if total and done > total:
            # 上一次续传写多了（服务端不支持 Range 时会从头返回），重下
            target.unlink()
            done = 0

        headers = {"Range": f"bytes={done}-"} if done else {}
        print(f"  下载 {filename}（第 {attempt} 次，已下 {done / 1024 / 1024:.0f}/"
              f"{total / 1024 / 1024:.0f} MB）")
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as response, target.open("ab" if done else "wb") as fh:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r    {done / total:.0%}", end="", flush=True)
        except Exception as exc:  # 网络中断很常见，继续续传即可
            print(f"\n    中断（{type(exc).__name__}），准备续传")
        print()

    if total and target.stat().st_size != total:
        raise RuntimeError(
            f"{filename} 下载不完整（{target.stat().st_size}/{total} 字节），请重跑本脚本续传"
        )
    return target


def read_rows(path: Path):
    """把 parquet 读成一行一个 dict。pyarrow 只在这里用到，核心库保持零依赖。"""
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    for row in table.to_pylist():
        yield row


def verify_entries(entries: list[CcEntry], jobs: int = 4):
    """用判题机筛掉「没有一份参考解能通过全部用例」的题。

    CodeContests 是研究数据集：同一道题可能挂着好几份 Python3 解，其中有错解、
    有 Python2 残留代码，用例里也混着自动生成时引入的错误期望。到底哪份解真的
    能过，只有跑一遍才知道。

    这道筛选保证了一件对本项目很关键的事：**题库里每道题的参考解都是 100% 通过的**。
    否则后面用参考解注入错误造出来的标签就是脏的——错因标签的可靠性完全建立在
    「参考解本来是对的」这个前提上。
    """
    verifier = CodeVerifier(sandbox=default_sandbox("local"), stop_on_first_failure=True)

    def check(entry: CcEntry):
        for source in entry.candidates:
            report = verifier.verify(source, entry.problem)
            if report.verdict is Verdict.AC:
                return entry, source
        return entry, None

    kept: list[tuple] = []
    dropped: list[str] = []
    total = len(entries)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for index, (entry, source) in enumerate(pool.map(check, entries), 1):
            if index % 100 == 0 or index == total:
                print(f"  验证 {index}/{total}", flush=True)
            if source is None:
                dropped.append(entry.id)
            else:
                kept.append((entry.problem, source))
    return kept, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 CodeContests 题库")
    parser.add_argument(
        "--download",
        type=int,
        default=0,
        metavar="N",
        help="先下载前 N 个训练分片（0 表示不下载，只导入本地已有的分片）",
    )
    parser.add_argument("--max-cases", type=int, default=8, help="每题最多保留几条用例")
    parser.add_argument("--limit", type=int, default=0, help="最多导入多少题（0 表示不限）")
    parser.add_argument("--jobs", type=int, default=6, help="验证参考解时的并发数")
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过参考解验证（快，但题库里会混进坏题）",
    )
    args = parser.parse_args()

    try:
        import pyarrow  # noqa: F401
    except ImportError:
        print("缺少 pyarrow，请先安装：pip install pyarrow")
        return 2

    raw_dir = ROOT / "data" / "raw" / "codecontests"
    raw_dir.mkdir(parents=True, exist_ok=True)

    print("=== CodeContests ===")
    if args.download:
        remote = list_remote_train_shards()
        wanted = [TEST_SHARD] + remote[: args.download]
        print(f"需要下载 {len(wanted)} 个分片（共 {len(remote)} 个训练分片可选）")
        for name in wanted:
            download_shard(name, raw_dir)

    files = sorted(raw_dir.glob("*.parquet"))
    if not files:
        print("没有可用的分片文件，请先加 --download N 下载")
        return 2
    print(f"使用 {len(files)} 个分片：{', '.join(p.stem[:22] for p in files[:3])}"
          + ("…" if len(files) > 3 else ""))

    # 逐个分片解析，避免把 3GB 数据一次性读进内存
    entries: list[CcEntry] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in files:
        part, part_skipped = convert_codecontests_entries(
            read_rows(path), max_cases=args.max_cases
        )
        for entry in part:
            if entry.id in seen:
                continue
            seen.add(entry.id)
            entries.append(entry)
        skipped.extend(part_skipped)
        print(f"  {path.name[:26]}… → 累计 {len(entries)} 道题", flush=True)

    if args.limit:
        entries = entries[: args.limit]

    dropped: list[str] = []
    if args.no_verify:
        kept = [(e.problem, e.candidates[0]) for e in entries]
    else:
        print(f"用判题机验证参考解（{len(entries)} 道，{args.jobs} 并发）…")
        kept, dropped = verify_entries(entries, jobs=args.jobs)

    problems = [problem for problem, _ in kept]
    references = [
        ReferenceSolution(id=problem.id, source=source, entry_point="")
        for problem, source in kept
    ]

    problems_path = save_problems_jsonl(problems, ROOT / "problems" / "codecontests.jsonl")
    refs_path = save_references(references, ROOT / "solutions" / "codecontests.jsonl")

    total_cases = sum(len(p.cases) for p in problems)
    ratings = [int(p.difficulty) for p in problems if str(p.difficulty).isdigit()]
    print(f"题目：{len(problems)} 道，用例共 {total_cases} 条"
          f"（平均 {total_cases / max(1, len(problems)):.1f} 条/题）")
    if ratings:
        print(f"难度（Codeforces rating）：最低 {min(ratings)}，最高 {max(ratings)}，"
              f"中位 {sorted(ratings)[len(ratings) // 2]}")
    print(f"参考解：{len(references)} 份（只有 Python3 的题才会进来）")
    print(f"写出：{problems_path.relative_to(ROOT)}、{refs_path.relative_to(ROOT)}")
    if dropped:
        print(f"验证未通过、已丢弃 {len(dropped)} 道：{', '.join(dropped[:8])}"
              + ("…" if len(dropped) > 8 else ""))
    if skipped:
        reasons: dict[str, int] = {}
        for _, reason in skipped:
            reasons[reason] = reasons.get(reason, 0) + 1
        print("跳过：")
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  - {reason}：{count} 道")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
