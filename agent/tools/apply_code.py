#!/usr/bin/env python3
"""apply_code.py — 将代码写入 LLVM 源文件（替换函数或指定区域）。

用法:
    # 方式 1: 整体替换文件中的 old_code → new_code（从 stdin 读取 new_code）
    echo '<new_code>' | python agent/tools/apply_code.py replace <file_path> '<old_code_first_line>' '<old_code_last_line>'

    # 方式 2: 直接用 stdin 内容写入文件指定行范围
    echo '<new_code>' | python agent/tools/apply_code.py write <file_path> <start_line> <end_line>

    # 方式 3: 用 sed 风格的替换（适合小改动）
    python agent/tools/apply_code.py sed <file_path> '<old_string>' '<new_string>'

复用: scripts/llvm_helper.py
"""

import os
import sys

import _setup  # noqa: F401
import llvm_helper


def cmd_replace(file_path, old_first_line, old_last_line):
    """从 stdin 读取新代码，在文件中找到 old_first_line...old_last_line 的区域并替换。"""
    new_code = sys.stdin.read()
    if not new_code.strip():
        print("ERROR: stdin 为空，没有收到新代码", file=sys.stderr)
        sys.exit(1)

    full_path = os.path.join(llvm_helper.llvm_dir, file_path)
    if not os.path.isfile(full_path):
        print(f"ERROR: 文件不存在: {full_path}", file=sys.stderr)
        sys.exit(1)

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 找到 old region: first_line 到 last_line
    old_first_line = old_first_line.strip()
    old_last_line = old_last_line.strip()

    lines = content.splitlines(keepends=True)
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if start_idx is None and old_first_line in line.strip():
            start_idx = i
        if start_idx is not None and old_last_line in line.strip():
            end_idx = i + 1
            break

    if start_idx is None or end_idx is None:
        print(f"ERROR: 未找到匹配区域。first_line='{old_first_line}', last_line='{old_last_line}'", file=sys.stderr)
        sys.exit(1)

    old_code = "".join(lines[start_idx:end_idx])
    new_content = content.replace(old_code, new_code, 1)

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"OK: 已替换 {file_path} 第 {start_idx+1}-{end_idx} 行（共 {end_idx - start_idx} 行 → {len(new_code.splitlines())} 行）")


def cmd_write(file_path, start_line, end_line):
    """从 stdin 读取新代码，替换文件的 [start_line, end_line] 行。"""
    new_code = sys.stdin.read()
    if not new_code.strip():
        print("ERROR: stdin 为空，没有收到新代码", file=sys.stderr)
        sys.exit(1)

    full_path = os.path.join(llvm_helper.llvm_dir, file_path)
    if not os.path.isfile(full_path):
        print(f"ERROR: 文件不存在: {full_path}", file=sys.stderr)
        sys.exit(1)

    start_line = int(start_line)
    end_line = int(end_line)

    with open(full_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 替换 [start_line, end_line]（1-indexed）
    new_lines = new_code.splitlines(keepends=True)
    if new_code and not new_code.endswith("\n"):
        new_lines[-1] += "\n"

    result = lines[:start_line - 1] + new_lines + lines[end_line:]
    with open(full_path, "w", encoding="utf-8") as f:
        f.writelines(result)

    print(f"OK: 已替换 {file_path} 第 {start_line}-{end_line} 行（共 {end_line - start_line + 1} 行 → {len(new_lines)} 行）")


def cmd_sed(file_path, old_string, new_string):
    """简单的字符串替换。"""
    full_path = os.path.join(llvm_helper.llvm_dir, file_path)
    if not os.path.isfile(full_path):
        print(f"ERROR: 文件不存在: {full_path}", file=sys.stderr)
        sys.exit(1)

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    count = content.count(old_string)
    if count == 0:
        print(f"ERROR: 未找到要替换的字符串: '{old_string}'", file=sys.stderr)
        sys.exit(1)

    new_content = content.replace(old_string, new_string)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"OK: 在 {file_path} 中替换了 {count} 处")


def main():
    if len(sys.argv) < 3:
        print("用法:", file=sys.stderr)
        print("  python apply_code.py replace <file> '<first_line>' '<last_line>'  (stdin=新代码)", file=sys.stderr)
        print("  python apply_code.py write <file> <start> <end>                   (stdin=新代码)", file=sys.stderr)
        print("  python apply_code.py sed <file> '<old>' '<new>'", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "replace":
        if len(sys.argv) < 5:
            print("ERROR: replace 模式需要 4 个参数", file=sys.stderr)
            sys.exit(1)
        cmd_replace(sys.argv[2], sys.argv[3], sys.argv[4])
    elif mode == "write":
        if len(sys.argv) < 5:
            print("ERROR: write 模式需要 4 个参数", file=sys.stderr)
            sys.exit(1)
        cmd_write(sys.argv[2], sys.argv[3], sys.argv[4])
    elif mode == "sed":
        if len(sys.argv) < 5:
            print("ERROR: sed 模式需要 4 个参数", file=sys.stderr)
            sys.exit(1)
        cmd_sed(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print(f"ERROR: 未知模式 '{mode}'", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
