#!/usr/bin/env python3
# Copyright 2025 Yingwei Zheng
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""git bisect 的单提交判定脚本。

该脚本由 bisect_driver.py 调用，或被 `git bisect run` 直接调用：
1. 尝试为目标提交准备 opt/lli 二进制；
2. 用数据集测试判断该提交是否可复现 bug（BAD）或已修复（GOOD）；
3. 若环境/构建/测试状态不稳定，则返回 SKIP（125）让 bisect 跳过。
"""


import os
import sys
import json
import subprocess
import llvm_helper
# import resource

GOOD = 0
BAD = 1
SKIP = 125

provider = os.environ.get("LAB_PROVIDER")


def test(commit_sha: str, issue_path: str) -> int:
    """评估单个提交在指定 issue 上的状态。

    返回值遵循 git bisect run 约定：
    - 0: GOOD（提交不复现问题，或已经包含修复）
    - 1: BAD（提交可稳定复现问题）
    - 125: SKIP（无法可靠判定）
    """
    with open(issue_path) as f:
        content = f.read()
        data = json.loads(content)

    # 绝大多数 case 只需要 opt；如果测试里包含 lli 期望输出则也要求 lli。
    required_binaries = ["opt"]
    if "lli_expected_out" in content:
        required_binaries.append("lli")

    bin_dir = os.path.join(llvm_helper.llvm_build_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    bug_type = data["bug_type"]
    # _, hard = resource.getrlimit(resource.RLIMIT_AS)
    # resource.setrlimit(resource.RLIMIT_AS, (min(hard, 8 * 1024**3), hard))
    skip_reason = ""
    try:
        for binary in required_binaries:
            target_file = os.path.join(bin_dir, binary)
            # 每次都删除旧产物，确保 provider 构建的是当前 commit 的新二进制。
            if os.path.exists(target_file):
                os.remove(target_file)
            skip_reason = f"failed to build {binary}"
            subprocess.check_call(
                [provider, commit_sha, binary, target_file],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # 先做最小健康检查，避免把损坏二进制带入后续测试。
            skip_reason = f"{binary} is not functional"
            subprocess.check_output([target_file, "--version"])

        # repro=True: 能复现表示该提交仍然是“坏”的。
        skip_reason = "test case cannot be reproduced"
        res, _ = llvm_helper.verify_test_group(
            repro=True, input=data["tests"], type=bug_type
        )
        if res:
            print(commit_sha, "BAD", file=sys.stderr)
            return BAD

        # repro=False: 能通过验证表示该提交“好”。
        skip_reason = "test case cannot be verified"
        res, _ = llvm_helper.verify_test_group(
            repro=False, input=data["tests"], type=bug_type
        )
        if res:
            print(commit_sha, "GOOD", file=sys.stderr)
            return GOOD
    except Exception:
        pass
    print(commit_sha, f"SKIP (reason={skip_reason})", file=sys.stderr)
    return SKIP


if __name__ == "__main__":
    # CLI:
    #   python3 bisect_runner.py <issue_json_path> [commit_sha]
    # 未提供 commit 时，默认读取 git bisect run 提供的 BISECT_HEAD。
    issue_path = sys.argv[1]
    commit_sha = sys.argv[2] if len(sys.argv) == 3 else llvm_helper.git_execute(["rev-parse", "BISECT_HEAD"]).strip()
    exit(test(commit_sha, issue_path))
