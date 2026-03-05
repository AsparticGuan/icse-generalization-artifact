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

"""批量扫描 LLVM issue 并触发数据抽取。

该脚本是数据集构建入口之一，职责为：
1. 在给定 issue id 区间内遍历 GitHub issue；
2. 依据标签/状态做快速过滤，仅保留候选优化问题；
3. 调用 postfix_extract.py 做深度解析与落盘；
4. 结合缓存与速率限制处理，支持长时间稳定运行。
"""

import os
import requests
import subprocess
from pathlib import Path
import time
import tqdm
import llvm_helper

# 访问 GitHub API 所需 Token 与本地缓存目录。
github_token = os.environ["LAB_GITHUB_TOKEN"]
cache_dir = os.environ["LAB_ISSUE_CACHE"]
# 复用后处理抽取脚本。
postfix_extract = os.path.join(os.path.dirname(__file__), "postfix_extract.py")

# 全局会话统一附带鉴权与 API 版本头。
session = requests.Session()
session.headers.update(
    {
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }
)

# issue 扫描区间（含两端）。
issue_id_begin = 76663  # 76663 Since 2024-01-01
issue_id_end = 177448


def wait(progress):
    """在限流或网络异常时等待下一轮可用窗口。"""
    try:
        rate_limit = session.get("https://api.github.com/rate_limit", timeout=10).json()
        if rate_limit["rate"]["remaining"] == 0:
            next_window = rate_limit["rate"]["reset"]
            while time.time() < next_window:
                progress.set_description(f"wait {int(next_window - time.time())}s")
                time.sleep(10)
    except Exception:
        time.sleep(60)
        pass


def fetch(issue_id):
    """拉取并筛选单个 issue，必要时调用 postfix_extract 做数据生成。"""
    data_json_path = os.path.join(llvm_helper.dataset_dir, f"{issue_id}.json")
    # 若数据已存在，则视为已处理。
    if os.path.exists(data_json_path):
        return False

    issue_url = f"https://api.github.com/repos/llvm/llvm-project/issues/{issue_id}"
    issue = session.get(issue_url).json()

    # 过滤不存在/被删除 issue。
    if "message" in issue and (
        issue["message"] == "Not Found" or issue["message"] == "This issue was deleted"
    ):
        return False

    # 仅关注“已关闭且完成”的 issue。
    if issue["state"] != "closed" or issue["state_reason"] != "completed":
        return False

    # 明确排除 PR（只处理 issue）。
    if "issue" not in issue["html_url"]:
        return False

    # 标签规则：需要包含目标类型标签，并排除明显无关子系统。
    has_valid_label = False
    is_llvm_middleend = False
    for label in issue["labels"]:
        label_name = label["name"]
        if label_name == "missed-optimization":
            has_valid_label = True
        if "llvm" in label_name or label_name in [
            "vectorizers",
            "loopoptim",
            "floating-point",
            "vectorization",
        ]:
            is_llvm_middleend = True
        for key in [
            "backend",
            "clang:",
            "clangd",
            "clang-tidy",
            "clang-format",
            "mlir",
            "tools:",
            "flang:",
            "lld:",
            "lldb",
            "tablegen",
            "polly",
            "PGO",
        ]:
            if key in label_name:
                return False
        if label_name in [
            "invalid",
            "wontfix",
            "duplicate",
            "undefined behavior",
            "llvm:SelectionDAG",
            "llvm:globalisel",
            "llvm:regalloc",
            "llvm:codegen",
            "llvm-reduce",
            "llvm:bitcode",
            "llvm:openmpirbuilder",
            "BOLT",
            "mc",
            "libc++",
            "coroutines",
        ]:
            return False
    if not has_valid_label:
        return False
    # if not is_llvm_middleend:
    #     return False

    # 通过基础筛选后，调用 postfix_extract.py 做 fix commit/patch/test 提取。
    try:
        out = subprocess.check_output(
            ["python3", postfix_extract, str(issue_id)], stderr=subprocess.DEVNULL
        ).decode()
        if "This issue is marked as invalid" in out:
            return False
        print(f"{issue_id}: {out}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"{issue_id}: {e}")
        return True


# 初始化缓存目录；缓存文件存在表示该 issue 已经判定过（成功或失败）。
os.makedirs(cache_dir, exist_ok=True)
success = 0
progress = tqdm.tqdm(range(issue_id_begin, issue_id_end + 1))
for issue_id in progress:
    progress.set_description(f"Success {success}")
    cache_file = os.path.join(cache_dir, str(issue_id))
    if os.path.exists(cache_file):
        progress.refresh()
        continue
    while True:
        try:
            if fetch(issue_id):
                success += 1
            else:
                # 未抽取成功时也写缓存哨兵，避免下次重复请求。
                Path(cache_file).touch()
            break
        # 以下异常统一走 wait，尽量保证长任务可恢复。
        except KeyError as e:
            wait(progress)
        except requests.exceptions.RequestException:
            wait(progress)
        except ValueError:
            wait(progress)
        except Exception as e:
            print(type(e), e)
            exit(0)
