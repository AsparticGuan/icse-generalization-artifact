#!/usr/bin/env python3
"""get_langref.py — 查询 LLVM IR 指令/intrinsic 文档。

用法:
    python agent/tools/get_langref.py <instruction_name> [instruction_name2 ...]

示例:
    python agent/tools/get_langref.py add
    python agent/tools/get_langref.py llvm.ctpop llvm.ctlz

输出 LangRef.rst 中对应关键字的章节片段。
复用: scripts/llvm_helper.py, scripts/lab_env.py
"""

import os
import sys

import _setup  # noqa: F401
from _setup import get_env_or_die

import llvm_helper
from lab_env import Environment as Env


def main():
    if len(sys.argv) < 2:
        print("用法: python get_langref.py <instruction> [instruction2 ...]", file=sys.stderr)
        sys.exit(1)

    keywords = sys.argv[1:]
    issue_id = os.environ.get("AGENT_ISSUE_ID")
    basemodel_cutoff = os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")

    if issue_id:
        env = Env(issue_id, basemodel_cutoff)
        base_commit = env.get_base_commit()
    else:
        # 不依赖 issue，直接用 HEAD
        base_commit = llvm_helper.git_execute(["rev-parse", "HEAD"]).strip()

    descs = llvm_helper.get_langref_desc(keywords, base_commit)

    for kw in keywords:
        if kw in descs:
            print(f"=== {kw} ===")
            text = descs[kw]
            if len(text) > 4000:
                text = text[:4000] + "\n<Truncated>..."
            print(text)
            print()
        else:
            print(f"=== {kw} === (not found in LangRef.rst)")
            print()


if __name__ == "__main__":
    main()
