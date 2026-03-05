#!/usr/bin/env python3
"""check_full.py — 完整验证（build + verify_test_group + llvm-lit 回归测试）。

用法:
    python agent/tools/check_full.py

需要环境变量:
    AGENT_ISSUE_ID          当前 issue ID
    LAB_LLM_BASEMODEL_CUTOFF  模型知识截止日期

输出: 构建状态 + 快速检查 + lit 回归测试结果。
复用: scripts/lab_env.py
"""

import os
import sys
import json
import re

import _setup  # noqa: F401
from _setup import get_env_or_die

import llvm_helper
from lab_env import Environment as Env

MAX_LOG_SIZE = 8000


def normalize_feedback(log) -> str:
    """从 lit 日志中提取 Check file 相关的失败信息。"""
    if isinstance(log, list):
        return json.dumps(log, indent=2)[:MAX_LOG_SIZE]
    text = str(log)
    matches = re.compile(r"(Check file:.*?>>>>>>)", re.DOTALL).findall(text)
    if matches:
        extracted = "\n\n".join(matches)
        if len(extracted) > MAX_LOG_SIZE:
            return extracted[:MAX_LOG_SIZE] + "\n<Truncated>..."
        return extracted
    if len(text) > MAX_LOG_SIZE:
        return text[:MAX_LOG_SIZE] + "\n<Truncated>..."
    return text


def main():
    issue_id = get_env_or_die("AGENT_ISSUE_ID")
    basemodel_cutoff = os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")
    max_build_jobs = int(os.environ.get("LAB_MAX_BUILD_JOBS", os.cpu_count()))

    env = Env(issue_id, basemodel_cutoff, max_build_jobs=max_build_jobs)

    print("=== Full Check (build + fast check + llvm-lit) ===")
    print(f"LLVM dir: {llvm_helper.llvm_dir}")
    print(f"Build dir: {llvm_helper.llvm_build_dir}")
    print()

    res, log = env.check_full()

    if res:
        print("✓ FULL CHECK PASSED (build + verify + lit)")
    else:
        if env.build_failure_count > 0 and not isinstance(log, list):
            print("✗ BUILD FAILED")
            print()
            # 提取构建错误
            pattern = re.compile(r"(llvm-project/llvm/[^\n]+error:[^\n]+(?:\n[ ]*\d+\s*\|[^\n]*)?)")
            matches = pattern.findall(str(log))
            if matches:
                print("\n".join(matches)[:MAX_LOG_SIZE])
            else:
                print(str(log)[:MAX_LOG_SIZE])
        elif not env.fast_check_pass:
            print("✓ BUILD SUCCESS")
            print("✗ FAST CHECK FAILED")
            print()
            # 输出测试结果
            if isinstance(log, list):
                for test_log in log:
                    name = test_log.get("name", "unknown")
                    result = test_log.get("result", False)
                    print(f"  [{'PASS' if result else 'FAIL'}] {name}")
                    if not result:
                        log_data = test_log.get("log", {})
                        if isinstance(log_data, dict):
                            cur = log_data.get("current_optimized_program", "")
                            exp = log_data.get("expect_optimized_program", "")
                            if cur:
                                print(f"    Current:\n```llvm\n{cur}\n```")
                            if exp:
                                print(f"    Expected:\n```llvm\n{exp}\n```")
            else:
                print(str(log)[:MAX_LOG_SIZE])
        else:
            print("✓ BUILD SUCCESS + FAST CHECK PASSED")
            print("✗ LIT REGRESSION TEST FAILED")
            print()
            print(normalize_feedback(log))


if __name__ == "__main__":
    main()
