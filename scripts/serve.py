#!/usr/bin/env python3
"""启动 Web 服务：浏览器里做题、提交、看诊断和掌握度。

用法：
    python scripts/serve.py                     # 默认 127.0.0.1:8000，本地沙箱
    python scripts/serve.py --port 8080
    python scripts/serve.py --host 0.0.0.0      # 同一局域网内其它机器也能打开
    python scripts/serve.py --sandbox docker    # 用容器隔离（更安全，但每次判题更慢）

启动后浏览器打开 http://127.0.0.1:8000 即可，不需要命令行操作。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description="编程题诊断与陪练 · Web 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只允许本机访问")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--sandbox",
        default="local",
        choices=["local", "docker", "auto"],
        help="判题沙箱：local 快、docker 安全但慢（默认 local）",
    )
    parser.add_argument("--workers", type=int, default=2, help="同时判题的线程数")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("缺少 Web 依赖，请先安装：pip install -e '.[web]'")
        return 2

    from code_judge.service import create_app

    print(f"正在加载题库与技能图谱（{ROOT}）…")
    started = time.monotonic()
    app = create_app(root=ROOT, sandbox=args.sandbox, workers=args.workers)
    info = app.state.runtime.info()
    print(
        f"就绪：题目 {info['problems']} 道 · 技能 {info['skills']} 项 · "
        f"沙箱 {info['sandbox']}（{info['sandbox_reason']}）"
        f"　加载耗时 {time.monotonic() - started:.1f}s"
    )
    if args.host == "0.0.0.0" and not app.state.runtime.sandbox.enforces_network_isolation:
        print("提示：当前沙箱没有网络隔离，请只在可信网络里监听 0.0.0.0。")
    print(f"浏览器打开 http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
