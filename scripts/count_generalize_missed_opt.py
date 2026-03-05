#!/usr/bin/env python3
"""
统计 results/generalize/deepseek-v3.2 里每个 issue 的 validation 中
missed_optimization 为 true 的 test case 个数。
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENERALIZE_DIR = PROJECT_ROOT / "results" / "generalize" / "deepseek-v3.2"

# 表格中的 12 个 issue（retest-fixes-*-orig-single）
TABLE_ISSUES = [
    "133344", "142593", "152948", "157524", "58523",
    "60167", "69803", "73622", "76623", "85250", "94737", "98800",
]


def main():
    if len(sys.argv) > 1:
        gen_dir = Path(sys.argv[1])
        if not gen_dir.is_absolute():
            gen_dir = PROJECT_ROOT / gen_dir
        issue_ids = TABLE_ISSUES  # 仍用表格的 issue，只换目录
    else:
        gen_dir = GENERALIZE_DIR
        issue_ids = TABLE_ISSUES

    if not gen_dir.exists():
        print(f"目录不存在: {gen_dir}")
        return

    rows = []
    for issue_id in issue_ids:
        path = gen_dir / f"{issue_id}.json"
        if not path.exists():
            rows.append((issue_id, None, None, "no file"))
            continue
        with open(path) as f:
            data = json.load(f)
        validation = data.get("validation") or []
        total = len(validation)
        missed_true = sum(1 for v in validation if v.get("missed_optimization") is True)
        rows.append((issue_id, total, missed_true, None))

    print("issue_id\tvalidation_total\tmissed_optimization=true")
    print("-" * 50)
    for r in rows:
        issue_id, total, missed_true, err = r
        if err:
            print(f"{issue_id}\t-\t{err}")
        else:
            print(f"{issue_id}\t{total}\t{missed_true}")
    print("-" * 50)
    valid = [r for r in rows if r[1] is not None]
    if valid:
        t_total = sum(r[1] for r in valid)
        t_missed = sum(r[2] for r in valid)
        print(f"合计: {len(valid)} 个 issue, validation 共 {t_total} 个 test")
        print(f"  其中 missed_optimization 为 true: {t_missed} 个")


if __name__ == "__main__":
    main()
