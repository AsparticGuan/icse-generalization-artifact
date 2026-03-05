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

"""聚合数据集中高频“疑似关键函数名”。

处理逻辑：
1. 遍历数据集 JSON，读取 hints.bug_location_funcname；
2. 过滤掉命名空间函数（含 ::）及非关注函数名；
3. 统计频次并按降序打印，帮助做热点函数分析。
"""

import os
import llvm_helper
import json

# funcnames: {函数名: 出现次数}
funcnames = dict()
task_list = []

# 扫描整个数据集目录，并从每条样本中提取函数级定位信息。
for name in os.listdir(llvm_helper.dataset_dir):
    if name.endswith(".json"):
        path = os.path.join(llvm_helper.dataset_dir, name)
        with open(path) as f:
            data = json.load(f)
            funcname = data["hints"]["bug_location_funcname"]
            for k, v in funcname.items():
                for key in v:
                    # 跳过类/命名空间限定函数，聚焦短函数名分布。
                    if "::" in key:
                        continue
                    # 复用 helper 过滤无分析价值的函数名（如过泛化命名）。
                    if not llvm_helper.is_interesting_funcname(key):
                        continue
                    funcnames[key] = funcnames.get(key, 0) + 1

# 按频次降序输出。
dist = sorted(funcnames.items(), key=lambda x: x[1], reverse=True)
for k, v in dist:
    print(k, v)
