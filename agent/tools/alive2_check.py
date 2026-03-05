#!/usr/bin/env python3
"""alive2_check.py — Alive2 语义正确性验证。

用法:
    # 方式 1: 传入两段 IR 文本（通过参数文件）
    python agent/tools/alive2_check.py <src_file.ll> <tgt_file.ll>

    # 方式 2: 从 stdin 读取，用 '---' 分隔 src 和 tgt
    python agent/tools/alive2_check.py --stdin

输出: Alive2 验证结果（PASS/FAIL + 详细日志）。
复用: scripts/llvm_helper.py
"""

import os
import sys

import _setup  # noqa: F401
import llvm_helper


def main():
    if len(sys.argv) < 2:
        print("用法:", file=sys.stderr)
        print("  python alive2_check.py <src.ll> <tgt.ll>", file=sys.stderr)
        print("  python alive2_check.py --stdin  (用 '---' 分隔 src 和 tgt)", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--stdin":
        content = sys.stdin.read()
        parts = content.split("---")
        if len(parts) < 2:
            print("ERROR: stdin 中需要用 '---' 分隔 src 和 tgt", file=sys.stderr)
            sys.exit(1)
        src = parts[0].strip()
        tgt = parts[1].strip()
    else:
        if len(sys.argv) < 3:
            print("ERROR: 需要两个文件参数", file=sys.stderr)
            sys.exit(1)
        with open(sys.argv[1], "r") as f:
            src = f.read()
        with open(sys.argv[2], "r") as f:
            tgt = f.read()

    # 自动补 datalayout（与 pipeline 中的逻辑一致）
    if "ptr" in src and "target datalayout" not in src:
        src = f'target datalayout = "p:8:8:8"\n{src}'
    if "ptr" in tgt and "target datalayout" not in tgt:
        tgt = f'target datalayout = "p:8:8:8"\n{tgt}'

    print("=== Alive2 Verification ===")
    res, log = llvm_helper.alive2_check(src, tgt, "-src-unroll=8 -tgt-unroll=8")

    if res:
        print("✓ PASS — The optimization is correct.")
    else:
        print("✗ FAIL — The optimization may be incorrect.")

    if isinstance(log, dict):
        a2log = log.get("log", "")
        if a2log:
            print()
            text = str(a2log)
            if len(text) > 4000:
                text = text[:4000] + "\n<Truncated>..."
            print(text)
    elif isinstance(log, str):
        print()
        if len(log) > 4000:
            log = log[:4000] + "\n<Truncated>..."
        print(log)


if __name__ == "__main__":
    main()
