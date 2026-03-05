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

"""将修复结果目录打包成统一提交 JSON。

该脚本用于生成对外提交/评测所需的聚合文件，核心行为：
1. 从环境变量读取方法与基座模型元信息；
2. 仅收集已在数据集中标记 verified 的样本；
3. 合并 fix_dir 中同名 JSON 修复证据，输出到单一 JSON 文件。
"""

import os
import sys
import json
import llvm_helper

# 由实验环境注入的方法信息，用于提交记录与可追溯性。
method_name = os.environ["LAB_METHOD_NAME"]
method_url = os.environ["LAB_METHOD_URL"]
base_model_name = os.environ["LAB_BASE_MODEL_NAME"]
base_model_url = os.environ["LAB_BASE_MODEL_URL"]

# 参数 1：修复证据目录（通常包含若干 {issue_id}.json）。
fix_dir = sys.argv[1]
# 参数 2：聚合输出文件路径。
output_file = sys.argv[2]

with open(output_file, "w") as f:
    # fixes: 最终提交的逐样本记录列表
    fixes = []
    # with_hint: 是否使用过任何 hint（通过 cert["knowledge"] 中的 key 判断）
    with_hint = False
    for file in os.listdir(fix_dir):
        if not file.endswith(".json"):
            continue
        # 从数据集目录读取元数据，确保该样本已通过验证再纳入提交。
        with open(os.path.join(llvm_helper.dataset_dir, file)) as info:
            info = json.load(info)
            if not info.get("verified", False):
                continue
            bug_id = info["bug_id"]
            bug_type = info["bug_type"]

        # 读取实际修复证据，并补齐 bug 元字段。
        with open(os.path.join(fix_dir, file)) as fix_file:
            cert = json.load(fix_file)
            if "knowledge" in cert:
                for k, v in cert["knowledge"]:
                    if k.startswith("hint"):
                        with_hint = True
                        break
            cert["bug_id"] = bug_id
            cert["bug_type"] = bug_type
            fixes.append(cert)

    # 输出统一提交结构，供后续上传/评分系统消费。
    json.dump(
        {
            "method_name": method_name,
            "method_url": method_url,
            "base_model_name": base_model_name,
            "base_model_url": base_model_url,
            "with_hint": with_hint,
            "fixes": fixes,
        },
        f,
    )
