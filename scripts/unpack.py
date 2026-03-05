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

"""将 submit 产出的聚合 JSON 结果拆分回逐样本 JSON 文件。

典型用途：
1. 输入一个包含 fixes 列表的大 JSON 文件；
2. 读取每条修复记录中的 bug_id；
3. 输出为 dataset_dir/{bug_id}.json，便于按样本回放或分析。
"""

import sys
import json
import os

# 参数 1：拆分后的目标目录。
dataset_dir = sys.argv[1]
# 参数 2：上游聚合文件（通常由 submit.py 生成）。
input_file = sys.argv[2]

os.makedirs(dataset_dir, exist_ok=True)

with open(input_file) as f:
    data = json.load(f)
    # 逐条展开 fixes，按 bug_id 命名为独立 JSON 文件。
    for item in data["fixes"]:
        issue_id = item['bug_id']
        with open(os.path.join(dataset_dir, f"{issue_id}.json"), "w") as out:
            # 为了方便人读和版本管理，使用缩进输出。
            json.dump(item, out, indent=2)
