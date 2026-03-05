#!/usr/bin/env python3
"""统计 results/generate/retest-fixes-* 里每个 issue 的 patch 通过了几个 test case。"""

import json
import sys
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "results/generate/retest-fixes-deepseek-v3.2-cot-iter4-orig"

def main():
    if len(sys.argv) > 1:
        dir_arg = sys.argv[1]
        DIR = Path(dir_arg)
        if not DIR.is_absolute():
            DIR = Path(__file__).resolve().parent.parent / dir_arg
    else:
        DIR = DEFAULT_DIR

    if not DIR.exists():
        print(f"目录不存在: {DIR}")
        return

    rows = []
    for f in sorted(DIR.glob("*.json")):
        issue_id = f.stem
        with open(f) as fp:
            data = json.load(fp)
        log = data.get("log") or {}
        retest_results = log.get("retest_results") or []
        total = 0
        passed = 0
        for item in retest_results:
            check_more = item.get("check_more") or {}
            cases = check_more.get("log")
            if not isinstance(cases, list):
                continue
            for tc in cases:
                if isinstance(tc, dict) and "result" in tc:
                    total += 1
                    if tc["result"] is True:
                        passed += 1
        rows.append((issue_id, passed, total))

    # 按 issue_id 排序输出
    print("issue_id\tpassed\ttotal")
    print("-" * 30)
    for issue_id, passed, total in rows:
        print(f"{issue_id}\t{passed}\t{total}")
    print("-" * 30)
    total_passed = sum(r[1] for r in rows)
    total_cases = sum(r[2] for r in rows)
    print(f"合计: {len(rows)} 个 issue, 共通过 {total_passed}/{total_cases} 个 test case")

if __name__ == "__main__":
    main()
