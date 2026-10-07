#!/usr/bin/env python3
"""下载并转换公开数据集，产出题库与参考解。

用法：
    python scripts/import_datasets.py                # 全部数据集
    python scripts/import_datasets.py humaneval      # 只导 HumanEval

产出：
    problems/<dataset>.jsonl    题库（不含答案）
    solutions/<dataset>.jsonl   参考解（变异测试的输入）
    data/raw/...                原始下载文件，保留以便离线重跑
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_judge.datasets import DATASETS, convert, download  # noqa: E402
from code_judge.problems import save_problems_jsonl  # noqa: E402
from code_judge.references import save_references  # noqa: E402


def main() -> int:
    wanted = sys.argv[1:] or list(DATASETS)
    unknown = [k for k in wanted if k not in DATASETS]
    if unknown:
        print(f"未知数据集：{unknown}；可选：{sorted(DATASETS)}")
        return 2

    raw_dir = ROOT / "data" / "raw"
    for key in wanted:
        spec = DATASETS[key]
        print(f"\n=== {spec.display} ===")
        raw_path = download(spec, raw_dir)
        print(f"原始数据：{raw_path.relative_to(ROOT)}（{raw_path.stat().st_size / 1024:.0f} KB）")

        result = convert(spec, raw_path)
        problems_path = save_problems_jsonl(result.problems, ROOT / "problems" / f"{key}.jsonl")
        refs_path = save_references(result.references, ROOT / "solutions" / f"{key}.jsonl")

        case_counts = [len(p.cases) for p in result.problems]
        total_cases = sum(case_counts)
        print(f"题目：{len(result.problems)} 道，用例共 {total_cases} 条"
              f"（平均 {total_cases / max(1, len(result.problems)):.1f} 条/题）")
        print(f"参考解：{len(result.references)} 份")
        print(f"写出：{problems_path.relative_to(ROOT)}、{refs_path.relative_to(ROOT)}")
        print(f"许可：{spec.license_note}")
        if result.skipped:
            print(f"跳过 {len(result.skipped)} 道：")
            for pid, reason in result.skipped[:5]:
                print(f"  - {pid}: {reason}")
            if len(result.skipped) > 5:
                print(f"  … 其余 {len(result.skipped) - 5} 道略")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
