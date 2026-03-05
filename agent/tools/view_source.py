#!/usr/bin/env python3
"""view_source.py — 查看 LLVM 源码指定位置。

用法:
    python agent/tools/view_source.py <file_path> [start_line] [end_line]

参数:
    file_path   相对于 llvm-project 根目录的文件路径（如 llvm/lib/Transforms/InstCombine/InstCombineCompares.cpp）
    start_line  起始行号（1-indexed，默认 1）
    end_line    结束行号（默认 start_line+50）

输出带行号的源码内容。
复用: scripts/llvm_helper.py
"""

import os
import sys

import _setup  # noqa: F401
import llvm_helper


def main():
    if len(sys.argv) < 2:
        print("用法: python view_source.py <file_path> [start_line] [end_line]", file=sys.stderr)
        sys.exit(1)

    file_path = sys.argv[1]
    # 安全检查
    if ".." in file_path:
        print("ERROR: 路径不能包含 '..'", file=sys.stderr)
        sys.exit(1)
    if not file_path.startswith("llvm/"):
        print("WARNING: 文件路径通常应以 'llvm/' 开头", file=sys.stderr)

    full_path = os.path.join(llvm_helper.llvm_dir, file_path)
    if not os.path.isfile(full_path):
        print(f"ERROR: 文件不存在: {full_path}", file=sys.stderr)
        sys.exit(1)

    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    total_lines = len(lines)
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    end = int(sys.argv[3]) if len(sys.argv) > 3 else min(start + 49, total_lines)

    start = max(1, min(start, total_lines))
    end = max(start, min(end, total_lines))

    print(f"File: {file_path} (lines {start}-{end} of {total_lines})")
    print("---")
    for i in range(start - 1, end):
        print(f"{i + 1:6d} | {lines[i]}", end="")
    # 确保最后一行有换行
    if lines and not lines[end - 1].endswith("\n"):
        print()


if __name__ == "__main__":
    main()
