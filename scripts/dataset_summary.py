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

"""统计数据集整体分布并输出汇总报告。

该脚本会遍历 LAB_DATASET_DIR 下的全部 issue JSON，计算：
1. 缺陷类型分布、组件分布、标签分布；
2. patch 粒度（文件数、hunk 数、增删行数）；
3. 测试规模与是否 verified 等质量指标。

输出以命令行文本为主，便于快速做数据健康检查。
"""

import llvm_helper
import os
import json
from unidiff import PatchSet

# 缺陷类型计数器。
bug_type = {
    "missed-optimization": 0,
    "miscompilation": 0,
    "crash": 0,
    "hang": 0,
}
# 组件分布（由 hints["components"] 提供）。
components = {}
# GitHub 标签分布。
labels = {}
# 样本总数。
count = 0

# 以下数组用于后续求均值/中位数/最值。
changed_files_count = []
ins_lines_count = []
del_lines_count = []
test_count = []

# patch 结构性质统计。
single_file_fix_count = 0
single_function_fix_count = 0
single_hunk_fix_count = 0

# 已通过 verify_repro 的样本数。
verified_count = 0

# 扫描整个数据集目录。
for name in os.listdir(llvm_helper.dataset_dir):
    if name.endswith(".json"):
        file = os.path.join(llvm_helper.dataset_dir, name)
        with open(file) as f:
            data = json.load(f)

        count += 1
        bug_type[data["bug_type"]] += 1

        # 组件层统计。
        hints = data["hints"]
        for component in hints["components"]:
            if component not in components:
                components[component] = 1
            else:
                components[component] += 1

        # 文件级修改范围统计。
        changed_files = len(hints["bug_location_lineno"])
        changed_files_count.append(changed_files)
        if changed_files == 1:
            single_file_fix_count += 1

        # 标签统计（来自 issue 元数据）。
        issue = data["issue"]
        for label in issue["labels"]:
            if label not in labels:
                labels[label] = 1
            else:
                labels[label] += 1

        # 行级 patch 统计。
        ins_lines = 0
        del_lines = 0
        patchset = PatchSet(data["patch"])
        hunk_count = 0
        for patch in patchset:
            for hunk in patch:
                hunk_count += 1
                ins_lines += hunk.added
                del_lines += hunk.removed
        if hunk_count == 1:
            single_hunk_fix_count += 1
        ins_lines_count.append(ins_lines)
        del_lines_count.append(del_lines)

        # 测试数量统计（每个 test 文件下再含多个子测试）。
        test_num = 0
        for test in data["tests"]:
            test_num += len(test["tests"])
        test_count.append(test_num)

        # 函数级定位统计：若只涉及 1 个函数（或单 hunk）则视为更聚焦的修复。
        bug_func = data["hints"]["bug_location_funcname"]
        bug_func_count = 0
        for k, v in bug_func.items():
            bug_func_count += len(v)
        if bug_func_count == 1 or hunk_count == 1:
            single_function_fix_count += 1

        # 验证通过计数。
        if data.get("verified", False):
            verified_count += 1


print(f"Total issues: {count}")
print(f"Verified issues: {verified_count} ({verified_count/count*100.0:.2f}%)")
print("\nBug type summary:")
for k, v in bug_type.items():
    print(f"  {k}: {v}")

print(f"\nBug component summary (Total = {len(components)}):")
for k, v in sorted(components.items(), key=lambda x: x[1], reverse=True):
    print(f"  {k}: {v}")

print("\nLabel summary:")
for k, v in sorted(labels.items(), key=lambda x: x[1], reverse=True):
    print(f"  {k}: {v}")

print("\nChanged files count summary:")
print(f"  Average: {sum(changed_files_count) / count:.2f}")
print(f"  Max: {max(changed_files_count)}")
print(f"  Min: {min(changed_files_count)}")
print(f"  Median: {sorted(changed_files_count)[count // 2]}")

print("\nInserted lines summary:")
print(f"  Average: {sum(ins_lines_count) / count:.2f}")
print(f"  Max: {max(ins_lines_count)}")
print(f"  Min: {min(ins_lines_count)}")
print(f"  Median: {sorted(ins_lines_count)[count // 2]}")

print("\nDeleted lines summary:")
print(f"  Average: {sum(del_lines_count) / count:.2f}")
print(f"  Max: {max(del_lines_count)}")
print(f"  Min: {min(del_lines_count)}")
print(f"  Median: {sorted(del_lines_count)[count // 2]}")

print("\nTest count summary:")
print(f"  Average: {sum(test_count) / count:.2f}")
print(f"  Max: {max(test_count)}")
print(f"  Min: {min(test_count)}")
print(f"  Median: {sorted(test_count)[count // 2]}")

print("\nPatch summary:")
print(
    f"  Single file fix: {single_file_fix_count} ({single_file_fix_count/count*100.0:.2f}%)"
)
print(
    f"  Single func fix: {single_function_fix_count} ({single_function_fix_count/count*100.0:.2f}%)"
)
print(
    f"  Single hunk fix: {single_hunk_fix_count} ({single_hunk_fix_count/count*100.0:.2f}%)"
)
