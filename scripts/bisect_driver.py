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

"""批量执行 LLVM issue 的 git bisect 驱动脚本。

核心目标：
1. 基于数据集中的 base_commit（通常是已知 bad）向历史回溯寻找 good 边界；
2. 调用 bisect_runner.py 作为单点判定器，让 git bisect 自动收敛到 first bad commit；
3. 将结果写回对应数据 JSON（字段 bisect）。
"""


import os
import sys
import llvm_helper
import json
import subprocess
import dateparser
from datetime import timezone

bisect_runner_file = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bisect_runner.py"
)


def is_llvm_functional_change(commit: str) -> bool:
    """判断候选提交是否触及 LLVM 中端功能代码。

当 bisect 无法精确定位到单个提交时，git 可能返回多个候选。
这里通过是否修改 llvm/lib 或 llvm/include 粗筛掉明显无关提交。
"""
    commit = commit.strip()
    changed_files = llvm_helper.git_execute(
        [
            "show",
            "--name-only",
            "--format=",
            commit,
            "--",
            "llvm/lib/*",
            "llvm/include/*",
        ]
    ).strip()
    return changed_files != ""


def bisect_issue(issue):
    """对单个 issue 数据执行 bisect，并回写结果。"""
    path = os.path.join(llvm_helper.dataset_dir, issue)
    with open(path) as f:
        data = json.load(f)
    if data["bug_type"] == "hang":
        # Not supported yet
        return

    # 若已有可解析的 bisect 结果，直接跳过，避免重复长时间计算。
    if "bisect" in data:
      commit = data["bisect"]
      try:
        commit_parsed = llvm_helper.git_execute(["rev-parse", commit]).strip()
        if commit_parsed == commit:
            return
      except subprocess.CalledProcessError:
        print(f"Previous bisect commit({commit}) is invalid. Redo bisect...")
        pass
    print(data["issue"]["title"])
    try:
        # base_commit 视为 BAD 端点。
        base_commit = data["base_commit"]  # bad
        base_commit_time = dateparser.parse(
            llvm_helper.git_execute(["show", "-s", "--format=%ci", base_commit]).strip()
        ).astimezone(timezone.utc)
        report_time = dateparser.parse(data["knowledge_cutoff"]).astimezone(
            timezone.utc
        )
        print("Report time:", report_time)
        print("Base commit time:", base_commit_time)
        if report_time < base_commit_time:
            # 若报告时间早于 base_commit，尝试把 BAD 端点调整到“报告前最近提交”，
            # 避免把模型本不该知道的未来信息引入 bisect 区间。
            commit_before_report = llvm_helper.git_execute(
                [
                    "log",
                    "--before",
                    report_time.isoformat(),
                    "--oneline",
                    "-n",
                    "1",
                    "--no-abbrev",
                    "--format=%H",
                ]
            ).strip()
            if (
                base_commit != commit_before_report
                and subprocess.run(
                    [bisect_runner_file, path, commit_before_report]
                ).returncode
                == 1
            ):
                base_commit = commit_before_report
                print("Adjusted base commit to", base_commit)
            else:
                print("Use original base commit (unavailable)")
        else:
            print("Use original base commit (newer)")

        good_commit = None
        offset = 100
        # 指数扩张向历史回退，直到找到一个 GOOD 点，减少线性探测开销。
        while offset <= 204800:  # ~5 years
            commit_sha = llvm_helper.git_execute(
                ["rev-parse", f"{base_commit}~{offset}"]
            ).strip()
            if subprocess.run([bisect_runner_file, path, commit_sha]).returncode == 0:
                good_commit = commit_sha
                break
            offset = int(offset * 1.6)
        if good_commit is None:
            raise RuntimeError("Cannot find a good commit")
        print("BAD", base_commit, "GOOD", good_commit)

        # 启动无 checkout 的 bisect，实际判定交给 bisect_runner.py。
        llvm_helper.git_execute(["bisect", "reset"])
        llvm_helper.git_execute(
            ["bisect", "start", "--no-checkout", base_commit, good_commit]
        )
        out = subprocess.check_output(
            [
                "git",
                "-C",
                llvm_helper.llvm_dir,
                "bisect",
                "run",
                bisect_runner_file,
                path,
            ],
            cwd=llvm_helper.llvm_dir,
            timeout=600.0,
        ).decode()
        if not out.endswith("bisect found first bad commit\n"):
            raise RuntimeError("Bisect failed: " + out)
        pos = out.rfind(" is the first bad commit\n")
        if pos == -1:
            raise RuntimeError("Bisect failed")
        pos2 = out.rfind("\n", 0, pos)
        if pos2 == -1:
            raise RuntimeError("Bisect failed")
        first_bad = out[pos2 + 1 : pos].strip()
        print(first_bad)
        data["bisect"] = first_bad
    except subprocess.TimeoutExpired:
        data["bisect"] = "Timeout"
        print("Timeout")
    except subprocess.CalledProcessError as e:
        # 某些情况下 bisect 只返回候选集合，这里做保守回退与筛选。
        out = e.stdout.decode()
        key = "The first bad commit could be any of:\n"
        pos = out.rfind(key)
        if pos == -1:
            return
        pos2 = out.find("We cannot bisect more!", pos)
        if pos2 == -1:
            return
        candidates = out[pos + len(key) : pos2].strip().splitlines()
        if len(candidates) > 1:
            candidates = list(filter(is_llvm_functional_change, candidates))
        # TODO: filter by pass name (not component name!)
        if len(candidates) == 0:
            data["bisect"] = "Unrelated"
        elif len(candidates) == 1:
            first_bad = candidates[0].strip()
            print(first_bad)
            data["bisect"] = first_bad
    except Exception as e:
        data["bisect"] = str(e)
        print(e)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


task_list = []
if len(sys.argv) == 2:
    # 传入单个 issue id 时，仅处理该样本。
    task_list = [sys.argv[1] + ".json"]
else:
    # 不带参数时遍历整个数据集。
    for name in os.listdir(llvm_helper.dataset_dir):
        if name.endswith(".json"):
            task_list.append(name)
task_list.sort(reverse=True)

for idx, task in enumerate(task_list):
    print("Bisecting", idx + 1, task.removesuffix(".json"))
    try:
        bisect_issue(task)
    except Exception as e:
        print(e)
        pass
