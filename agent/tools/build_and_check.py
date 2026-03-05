#!/usr/bin/env python3
"""build_and_check.py — 构建 LLVM 并运行快速验证。

用法:
    python agent/tools/build_and_check.py

需要环境变量:
    AGENT_ISSUE_ID          当前 issue ID
    LAB_LLM_BASEMODEL_CUTOFF  模型知识截止日期
    LAB_LLVM_DIR            LLVM 源码目录
    LAB_LLVM_BUILD_DIR      LLVM 构建目录（已含 issue 后缀）

输出: 构建状态 + 快速检查结果（逐测试 PASS/FAIL + IR 比对信息）。
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


def normalize_build_errors(log) -> str:
    """从构建日志中提取关键错误行。"""
    if isinstance(log, list):
        return json.dumps(log, indent=2)[:MAX_LOG_SIZE]
    text = str(log)
    pattern = re.compile(r"(llvm-project/llvm/[^\n]+error:[^\n]+(?:\n[ ]*\d+\s*\|[^\n]*)?)")
    matches = pattern.findall(text)
    if matches:
        result = "\n".join(matches)
        if len(result) > MAX_LOG_SIZE:
            return result[:MAX_LOG_SIZE] + "\n<Truncated>..."
        return result
    if len(text) > MAX_LOG_SIZE:
        return text[:MAX_LOG_SIZE] + "\n<Truncated>..."
    return text


def format_test_results(log) -> str:
    """格式化快速检查结果为人类可读文本。"""
    if not isinstance(log, list):
        text = str(log)
        if len(text) > MAX_LOG_SIZE:
            return text[:MAX_LOG_SIZE] + "\n<Truncated>..."
        return text

    output = []
    for test_log in log:
        name = test_log.get("name", "unknown")
        result = test_log.get("result", False)
        status = "PASS" if result else "FAIL"
        output.append(f"[{status}] {name}")

        if not result:
            log_data = test_log.get("log", {})
            if isinstance(log_data, dict):
                # 输出 source / expected / current IR 对比
                src = log_data.get("source_program", "")
                exp = log_data.get("expect_optimized_program", "")
                cur = log_data.get("current_optimized_program", "")
                if src:
                    output.append(f"  Source program:\n```llvm\n{src}\n```")
                if exp:
                    output.append(f"  Expected optimized:\n```llvm\n{exp}\n```")
                if cur:
                    output.append(f"  Current optimized:\n```llvm\n{cur}\n```")

                # Alive2 结果
                alive2 = log_data.get("alive2", {})
                if isinstance(alive2, dict):
                    a2log = alive2.get("log", "")
                    if a2log and "0 incorrect" not in str(a2log):
                        a2_text = str(a2log)
                        if len(a2_text) > 2000:
                            a2_text = a2_text[:2000] + "\n<Truncated>..."
                        output.append(f"  Alive2 log:\n{a2_text}")
            else:
                text = str(log_data)
                if len(text) > 2000:
                    text = text[:2000] + "\n<Truncated>..."
                output.append(f"  Log: {text}")

    return "\n".join(output)


def main():
    issue_id = get_env_or_die("AGENT_ISSUE_ID")
    basemodel_cutoff = os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")
    max_build_jobs = int(os.environ.get("LAB_MAX_BUILD_JOBS", os.cpu_count()))

    env = Env(issue_id, basemodel_cutoff, max_build_jobs=max_build_jobs)

    print("=== Building LLVM + Fast Check ===")
    print(f"LLVM dir: {llvm_helper.llvm_dir}")
    print(f"Build dir: {llvm_helper.llvm_build_dir}")
    print()

    res, log = env.check_fast(skip_build=False)

    if res:
        print("✓ BUILD SUCCESS + FAST CHECK PASSED")
        print()
        print(format_test_results(log))
    else:
        # 判断是构建失败还是测试失败
        if env.build_failure_count > 0 and not isinstance(log, list):
            print("✗ BUILD FAILED")
            print()
            print(normalize_build_errors(log))
        else:
            print("✓ BUILD SUCCESS")
            print("✗ FAST CHECK FAILED")
            print()
            print(format_test_results(log))


if __name__ == "__main__":
    main()
