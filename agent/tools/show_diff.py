#!/usr/bin/env python3
"""show_diff.py — 显示当前 LLVM 源码相对于 base commit 的 git diff。

用法:
    python agent/tools/show_diff.py

输出 llvm/lib/ 和 llvm/include/ 下的变更 diff。
复用: scripts/llvm_helper.py
"""

import os
import sys

import _setup  # noqa: F401
import llvm_helper

MAX_DIFF_SIZE = 10000


def main():
    print("=== Current diff (llvm/lib/ + llvm/include/) ===")
    try:
        diff = llvm_helper.git_execute(["diff", "--", "llvm/lib/*", "llvm/include/*"])
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if not diff.strip():
        print("(no changes)")
    else:
        if len(diff) > MAX_DIFF_SIZE:
            print(diff[:MAX_DIFF_SIZE])
            print(f"\n<Truncated — showing first {MAX_DIFF_SIZE} chars of {len(diff)}>")
        else:
            print(diff)


if __name__ == "__main__":
    main()
