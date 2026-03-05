#!/usr/bin/env python3
"""
统计：表格中每个 issue 的 retest test cases 里，
有多少个是「没有 missed_optimization 为 true」的 test case。

判定方式（与 pipeline 一致）：
- 来自 dataset 的 dev_tests，若 current_optimized_program == expect_optimized_program，
  则视为「已优化 / 非 missed optimization」；
- 若 current_optimized_program != expect_optimized_program，则视为 missed optimization。
因此「没有 missed_optimization 为 true」= current == expect 的个数。
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = PROJECT_ROOT / "dataset"

# 表格中的 issue 列表（retest-fixes-*-orig 的 10 个）
TABLE_ISSUES = [
    "133344", "142593", "152948", "157524", "58523",
    "60167", "73622", "76623", "94737", "98800",
]


def main():
    if len(sys.argv) > 1:
        # 可选：传入 retest 目录，从中读取实际有结果的 issue 列表
        retest_dir = Path(sys.argv[1])
        if not retest_dir.is_absolute():
            retest_dir = PROJECT_ROOT / retest_dir
        if retest_dir.exists():
            issue_ids = sorted(
                f.stem for f in retest_dir.glob("*.json")
                if (retest_dir / f.name).stat().st_size > 100
            )
            try:
                # 排除纯 error 的 json
                for iid in list(issue_ids):
                    with open(retest_dir / f"{iid}.json") as f:
                        if json.load(f).get("error"):
                            issue_ids.remove(iid)
            except Exception:
                pass
        else:
            issue_ids = TABLE_ISSUES
    else:
        issue_ids = TABLE_ISSUES

    rows = []
    for issue_id in issue_ids:
        dataset_path = DATASET_DIR / f"{issue_id}.json"
        if not dataset_path.exists():
            rows.append((issue_id, None, None, None, "no dataset"))
            continue
        with open(dataset_path) as f:
            data = json.load(f)
        dev_tests = data.get("dev_tests") or []
        total = 0
        missed_true = 0  # current != expect → missed optimization
        not_missed = 0   # current == expect → 没有 missed optimization 为 true
        for grp in dev_tests:
            for t in grp.get("tests") or []:
                total += 1
                cur = (t.get("current_optimized_program") or "").strip()
                exp = (t.get("expect_optimized_program") or "").strip()
                if cur != exp:
                    missed_true += 1
                else:
                    not_missed += 1
        rows.append((issue_id, total, missed_true, not_missed, None))

    print("issue_id\ttotal\tmissed_opt=true\t没有missed_opt为true")
    print("-" * 55)
    for r in rows:
        issue_id, total, missed_true, not_missed, err = r
        if err:
            print(f"{issue_id}\t-\t-\t{err}")
        else:
            print(f"{issue_id}\t{total}\t{missed_true}\t{not_missed}")
    print("-" * 55)
    valid = [r for r in rows if r[1] is not None]
    if valid:
        t_total = sum(r[1] for r in valid)
        t_missed = sum(r[2] for r in valid)
        t_not_missed = sum(r[3] for r in valid)
        print(f"合计: {len(valid)} 个 issue, 总 test case {t_total} 个")
        print(f"  其中 missed_optimization 为 true: {t_missed} 个")
        print(f"  没有 missed_optimization 为 true: {t_not_missed} 个")


if __name__ == "__main__":
    main()
