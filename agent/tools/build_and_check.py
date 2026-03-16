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


def collect_infra_failures(log) -> list[dict]:
    """提取 fast-check 中的基础设施失败条目。"""
    failures = []
    if not isinstance(log, list):
        return failures
    for test_log in log:
        data = test_log.get("log", {})
        if not isinstance(data, dict):
            continue
        if data.get("infra_error"):
            failures.append(
                {
                    "name": test_log.get("name", "unknown"),
                    "reason": data.get("infra_reason", "infra_error"),
                    "detail": data.get("alive2", {}).get("log", data.get("log", "")),
                }
            )
            continue
        alive2 = data.get("alive2", {})
        if isinstance(alive2, dict) and alive2.get("infra_error"):
            failures.append(
                {
                    "name": test_log.get("name", "unknown"),
                    "reason": alive2.get("infra_reason", "alive2_infra_error"),
                    "detail": alive2.get("log", ""),
                }
            )
    return failures


def normalize_build_errors(log) -> str:
    """从构建日志中提取关键错误行。"""
    if isinstance(log, list):
        return json.dumps(log, indent=2)[:MAX_LOG_SIZE]
    text = str(log)
    pattern = re.compile(
        r"(llvm-project/llvm/[^\n]+error:[^\n]+(?:\n[ ]*\d+\s*\|[^\n]*)?)"
    )
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
                if log_data.get("infra_error"):
                    reason = log_data.get("infra_reason", "infra_error")
                    output.append(f"  Infra error: {reason}")
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
                    if alive2.get("infra_error"):
                        output.append(
                            f"  Alive2 infra error: {alive2.get('infra_reason', 'unknown')}"
                        )
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
            infra_failures = collect_infra_failures(log)
            if infra_failures:
                print("! INFRA FAILURE DETECTED (toolchain/runtime)")
                for item in infra_failures[:3]:
                    print(f"  - {item['name']}: {item['reason']}")
                print()
            print(format_test_results(log))


if __name__ == "__main__":
    main()
