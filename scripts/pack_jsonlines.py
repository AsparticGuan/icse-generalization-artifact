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

"""将目录中的 JSON 样本打包为 JSON Lines（.jsonl）文件。

脚本定位：
1. 输入一个包含若干 JSON 文件的数据目录；
2. 逐条读取并写入到一个统一的 JSONL 输出文件中；
3. 常用于批量训练/评测前的样本归档与传输。
"""

import jsonlines
import json
import sys
import os

# 参数 1：待打包的数据目录（通常是 dataset/fixes 一类目录）
dataset_dir = sys.argv[1]
# 参数 2：输出 JSONL 文件路径
output_file = sys.argv[2]

with jsonlines.open(output_file, "w") as writer:
    # 逐文件读取；脚本默认目录下文件均为可被 json.load 解析的 JSON。
    for file in os.listdir(dataset_dir):
        with open(os.path.join(dataset_dir, file)) as f:
            # 每个输入文件写入 JSONL 的一行对象。
            writer.write(json.load(f))
