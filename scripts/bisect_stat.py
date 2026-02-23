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

"""统计并可视化 bisect 结果质量与时间跨度。

输出重点：
1. 可用 bisect 结果占比（能解析为有效 commit 的样本比例）；
2. 报告-修复、引入-修复、引入-报告 三种时间跨度分布（天）；
3. bisect 定位精度（文件级/行级与真实修复 patch 的重叠程度）。

最终会在 work/bisect_stat.png 生成直方图 + ECDF 图，辅助分析数据集时序与定位质量。
"""

import os
import subprocess
import llvm_helper
import json
import dateparser
from datetime import timezone
import math
from unidiff import PatchSet
import string
import matplotlib.pyplot as plt

# 数据集总样本数。
total = 0
# 具有可解析 bisect commit 的样本数。
available_count = 0

# 三种时间间隔统计（单位：天）。
report_to_fix_record = []
bug_to_fix_record = []
bug_to_report_record = []

# bisect 与真实修复 patch 的“相关性”统计。
related_count_file_level = 0
related_count_line_level = 0


def geomean(nums):
    """几何平均数，用于弱化极端值对时间统计的影响。"""
    log_sum = sum([math.log(x) for x in nums])
    return math.exp(log_sum / len(nums))


def analyze_patch(patch: str) -> dict:
    """把 patch 归一化为“文件 -> 规范化行集合”。

    规范化策略：
    - 去掉首尾空白；
    - 把标点/空白统一替换为空格；
    - 过滤过短（<16）片段，减少噪声。

    该结果用于和 bisect 定位提交做行级重叠比较。
    """
    lines = dict()
    patch_set = PatchSet(patch)
    special_chars = set(string.punctuation + string.whitespace)
    for patched_file in patch_set:
        filename = patched_file.path
        file_lines = set()
        for hunk in patched_file:
            for line in hunk:
                canonicalized = line.value.strip()
                canonicalized = "".join(
                    [" " if c in special_chars else c for c in canonicalized]
                )
                if len(canonicalized) < 16:
                    continue
                file_lines.add(canonicalized)
        lines[filename] = file_lines
    return lines


for name in os.listdir(llvm_helper.dataset_dir):
    if not name.endswith(".json"):
        continue
    total += 1
    path = os.path.join(llvm_helper.dataset_dir, name)
    with open(path) as f:
        data = json.load(f)

    # 跳过尚无 bisect 结果的样本。
    if "bisect" not in data:
        continue

    # 只有能被 git rev-parse 成功解析、且规范化后不变的哈希才算可用结果。
    run = subprocess.run(
        ["git", "-C", llvm_helper.llvm_dir, "rev-parse", data["bisect"]],
        capture_output=True,
    )
    if run.returncode != 0:
        continue
    if run.stdout.decode().strip() != data["bisect"]:
        continue

    # 计算三类时间戳：报告时间、引入 bug 提交时间、修复提交时间。
    report_time = dateparser.parse(data["knowledge_cutoff"]).astimezone(timezone.utc)
    bug_time = dateparser.parse(
        llvm_helper.git_execute(["show", "-s", "--format=%ci", data["bisect"]]).strip()
    ).astimezone(timezone.utc)
    fix_commit = data["hints"]["fix_commit"]
    fix_time = dateparser.parse(
        llvm_helper.git_execute(["show", "-s", "--format=%ci", fix_commit]).strip()
    ).astimezone(timezone.utc)
    bug_commit_title = llvm_helper.git_execute(
        ["show", "-s", "--format=%s", data["bisect"]]
    ).strip()
    fix_commit_title = llvm_helper.git_execute(
        ["show", "-s", "--format=%s", fix_commit]
    ).strip()
    print(bug_commit_title, "->", fix_commit_title)

    # 统一换算为天，便于横向比较。
    report_to_fix = (fix_time - report_time).total_seconds() / 86400
    bug_to_fix = (fix_time - bug_time).total_seconds() / 86400
    bug_to_report = (report_time - bug_time).total_seconds() / 86400

    # 若时间顺序异常（出现负值）则标记并跳过统计，避免污染总体结果。
    if report_to_fix < 0 or bug_to_fix < 0 or bug_to_report < 0:
        print("Invalid time data, skip")
        print(f"  report time: {report_time}")
        print(f"  bug time: {bug_time}")
        print(f"  fix time: {fix_time}")
        with open(path, "w") as f:
            data["bisect"] = "Invalid order"
            json.dump(data, f, indent=2)
        continue
    available_count += 1
    report_to_fix_record.append(report_to_fix)
    bug_to_fix_record.append(bug_to_fix)
    bug_to_report_record.append(bug_to_report)

    # bisect 精度评估：
    # - 文件级：是否命中修复所涉及文件
    # - 行级：规范化后是否命中至少一条相似修改行
    fix_lines = analyze_patch(data["patch"])
    buggy_patch = llvm_helper.git_execute(["show", data["bisect"], "--", "llvm/lib/*", "llvm/include/*"])
    buggy_lines = analyze_patch(buggy_patch)
    is_related_file_level = False
    is_related_line_level = False
    for filename, lines in fix_lines.items():
        if filename in buggy_lines:
            is_related_file_level = True
            if len(lines.intersection(buggy_lines[filename])) > 0:
                is_related_line_level = True
    if is_related_file_level:
        related_count_file_level += 1
    if is_related_line_level:
        related_count_line_level += 1

# 命令行文本汇总。
print(
    f"Available bisect result: {available_count}/{total} ({available_count/total*100:.2f}%)"
)
print(f"Average report to fix: {geomean(report_to_fix_record):.2f} days")
print(f"Average bug to fix: {geomean(bug_to_fix_record):.2f} days")
print(f"Average bug to report: {geomean(bug_to_report_record):.2f} days")
print(
    f"p95 bug to report: {sorted(bug_to_report_record)[int(len(bug_to_report_record)*0.95)]:.2f} days"
)
print(f"Bisection precision (file level): {related_count_file_level}/{available_count} ({related_count_file_level/available_count*100:.2f}%)")
print(f"Bisection precision (line level): {related_count_line_level}/{available_count} ({related_count_line_level/available_count*100:.2f}%)")

# 绘制 3 个时间维度的直方图 + ECDF。
fig = plt.figure(figsize=(9, 4), layout="constrained")
axs = fig.subplots(1, 3)
data_lists = {
    "Report to fix": report_to_fix_record,
    "Bug to fix": bug_to_fix_record,
    "Bug to report": bug_to_report_record,
}
for ax, (title, data) in zip(axs, data_lists.items()):
    ax2 = ax.twinx()
    ax.hist(data, bins=30)
    ax2.ecdf(data, color="orange")
    ax.set_title(title)
    ax.set_ylabel("Count")
    ax2.set_ylabel("Probability of occurrence")
    ax.set_xlabel("Duration (day)")
    ax.grid(True)
fig.savefig("work/bisect_stat.png", dpi=300)
plt.close(fig)
