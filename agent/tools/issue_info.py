#!/usr/bin/env python3
"""issue_info.py — 获取 issue 元信息和测试用例。

用法:
    python agent/tools/issue_info.py <issue_id>

输出 issue 的 bug_type、component、测试用例（source/expected/current IR）等信息。
复用: scripts/lab_env.py, scripts/llvm_helper.py
"""

import os
import sys
import json

import _setup  # noqa: F401 — 初始化 sys.path
from _setup import get_env_or_die

import llvm_helper
from lab_env import Environment as Env


def main():
    if len(sys.argv) < 2:
        print("用法: python issue_info.py <issue_id>", file=sys.stderr)
        sys.exit(1)

    issue_id = sys.argv[1]
    basemodel_cutoff = os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")

    env = Env(issue_id, basemodel_cutoff)

    # 基本信息
    bug_type = env.get_bug_type()
    components = list(env.get_hint_components() or [])
    bug_funcs = env.get_hint_bug_functions() or {}
    base_commit = env.get_base_commit()

    # Issue 描述
    issue = env.data.get("issue")
    issue_title = issue["title"] if issue else ""

    # 测试用例
    tests_info = []
    for test_file in env.get_tests():
        for test in test_file["tests"]:
            t = {
                "test_name": test["test_name"],
                "source_program": test.get("source_program", ""),
                "expect_optimized_program": test.get("expect_optimized_program", ""),
                "current_optimized_program": test.get("current_optimized_program", ""),
            }
            # 标记是否已经通过
            src = t["current_optimized_program"].strip()
            exp = t["expect_optimized_program"].strip()
            t["already_optimized"] = (src == exp) if src and exp else False
            tests_info.append(t)

    # 定位信息（hint）
    hint_files = []
    try:
        lineno = env.get_hint_line_level_bug_locations()
        if lineno:
            hint_files = list(lineno.keys())
    except Exception:
        pass

    # 输出
    print(f"=== Issue {issue_id} ===")
    print(f"Bug Type: {bug_type}")
    print(f"Components: {', '.join(components)}")
    print(f"Base Commit: {base_commit}")
    print(f"Issue Title: {issue_title}")
    print(f"Bug Functions (hint): {json.dumps(bug_funcs, ensure_ascii=False)}")
    if hint_files:
        print(f"Bug Files (hint): {', '.join(hint_files)}")
    print()

    # 打印未优化的测试用例
    for t in tests_info:
        if t["already_optimized"]:
            print(f"[PASS] Test {t['test_name']} — already optimized")
            continue
        print(f"[FAIL] Test {t['test_name']} — needs optimization")
        if t["source_program"]:
            print(f"Source program:\n```llvm\n{t['source_program']}\n```")
        if t["expect_optimized_program"]:
            print(f"Expected optimized program:\n```llvm\n{t['expect_optimized_program']}\n```")
        if t["current_optimized_program"]:
            print(f"Current (wrong) optimized program:\n```llvm\n{t['current_optimized_program']}\n```")
        print()


if __name__ == "__main__":
    main()
