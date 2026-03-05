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

"""查询并打印 GitHub API 当前速率限制状态。

该脚本常用于批量抓取 issue 前的健康检查：
1. 使用环境变量中的 Token 建立会话；
2. 调用 /rate_limit 接口；
3. 以格式化 JSON 输出，便于人工观察 remaining/reset 等字段。
"""

import os
import requests
import json

# GitHub Token 由实验环境注入，用于提高 API 速率上限。
github_token = os.environ["LAB_GITHUB_TOKEN"]
# 复用 requests.Session 以统一 headers、避免每次请求重复构造。
session = requests.Session()
session.headers.update(
    {
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }
)

# 直接输出 JSON，便于后续日志系统采集或命令行管道处理。
print(json.dumps(session.get("https://api.github.com/rate_limit").json(), indent=2))
