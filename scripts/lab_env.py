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

"""实验运行环境封装：围绕单个 issue 样本提供统一操作接口。

该模块把数据读取、构建、快速检查、完整检查、hint 访问等能力集中到 Environment 中，
用于 pipeline 与 baseline 脚本统一调用，避免在多个入口重复实现状态管理逻辑。
"""

import llvm_helper
import json
import os
import dateparser
import time
import datetime
import re
import subprocess
from typing import Tuple, Sequence, Union, Optional


class TimeCompensationGuard:
    """用于扣除“交互等待时间”的上下文管理器。

    典型场景：Environment 调用外部构建/测试时，会把该时间记入补偿项，
    从 wall_time 中扣除，避免把非算法时间算入评测成本。
    """

    def __init__(self, environment):
        self.environment = environment

    def __enter__(self):
        self.start_time = time.time()
        self.environment.interaction_time_compensation_enter += 1

    def __exit__(self, exception_type, exception_value, exception_traceback):
        self.environment.interaction_time_compensation_enter -= 1
        if self.environment.interaction_time_compensation_enter == 0:
            self.environment.interaction_time_compensation += (
                time.time() - self.start_time
            )


class Environment:
    """单个 issue 的执行上下文。

    核心职责：
    1. 维护 base_commit、bug_type、tests 等样本元数据；
    2. 提供 reset/build/check_fast/check_full 等标准动作；
    3. 记录知识使用轨迹、构建次数、检查日志等统计信息。
    """

    def __init__(
        self,
        issue_id,
        base_model_knowledge_cutoff: str,
        *,
        max_build_jobs=None,
        max_test_jobs=None,
        additional_cmake_args=[],
    ):
        # 读取样本 JSON，作为当前环境的单一事实来源。
        with open(os.path.join(llvm_helper.dataset_dir, f"{issue_id}.json")) as f:
            self.data = json.load(f)

        # 样本静态属性。
        self.base_commit = self.data["base_commit"]
        self.knowledge_cutoff = dateparser.parse(self.data["knowledge_cutoff"])
        self.bug_type = self.data["bug_type"]
        self.test_commit = self.data.get(
            "test_commit", self.data["hints"]["fix_commit"]
        )

        # 记录已使用知识源（url -> 最早使用时间）。
        self.used_knowledge = dict()
        self.use_knowledge("base_model", base_model_knowledge_cutoff)

        # 时间/计数统计指标。
        self.interaction_time_compensation = 0.0
        self.interaction_time_compensation_enter = 0
        self.build_count = 0
        self.build_failure_count = 0
        self.fast_check_count = 0
        self.full_check_count = 0
        self.fast_check_pass = False
        self.full_check_pass = False

        # 并行度配置：默认尽量使用机器可用核数。
        if max_build_jobs is None:
            self.max_build_jobs = os.cpu_count()
        else:
            self.max_build_jobs = max_build_jobs
        if max_test_jobs is None:
            self.max_test_jobs = max_build_jobs
        else:
            self.max_test_jobs = max_test_jobs

        # 合并运行时注入参数与样本额外 cmake 参数。
        self.additional_cmake_args = additional_cmake_args + self.data.get(
            "additional_cmake_args", []
        )

        # 运行日志状态。
        self.start_time = time.time()
        self.check_fast_log = []
        self.check_full_log = []
        self.prompted_tests = {}

    def use_knowledge(self, url: str, date: Union[str, datetime.datetime]):
        """登记一条知识使用记录，并检查是否越过 knowledge_cutoff。"""
        if isinstance(date, str):
            date = dateparser.parse(date)
        if date <= self.knowledge_cutoff:
            self.used_knowledge[url] = min(self.used_knowledge.get(url, date), date)
        else:
            raise ValueError("Knowledge is newer than the cutoff date")

    def reset(self):
        """把 LLVM 工作树重置到该样本 base_commit。"""
        with TimeCompensationGuard(self):
            llvm_helper.reset(self.base_commit)

    def verify_head(self):
        """确保当前 HEAD 与样本 base_commit 一致。"""
        head = llvm_helper.git_execute(["rev-parse", "HEAD"]).strip()
        if head != self.base_commit:
            raise RuntimeError("invalid HEAD")

    def build(self):
        """执行一次构建，并更新构建统计。"""
        with TimeCompensationGuard(self):
            self.build_count += 1
            self.verify_head()
            res, log = llvm_helper.build(
                self.max_build_jobs, self.additional_cmake_args
            )
            if not res:
                self.build_failure_count += 1
            return res, log

    def dump(self, log=None):
        """导出当前运行快照，可用于提交证据或调试落盘。"""
        wall_time = time.time() - self.start_time - self.interaction_time_compensation
        self.verify_head()
        patch = llvm_helper.git_execute(["diff", "--", "llvm/lib/*", "llvm/include/*"])
        used_knowledge = []
        for url, t in self.used_knowledge.items():
            used_knowledge.append((url, t.strftime("%Y-%m-%d%z")))
        return {
            "wall_time": wall_time,
            "knowledge": used_knowledge,
            "build_count": self.build_count,
            "build_failure_count": self.build_failure_count,
            "fast_check_count": self.fast_check_count,
            "full_check_count": self.full_check_count,
            "fast_check_pass": self.fast_check_pass,
            "full_check_pass": self.full_check_pass,
            "patch": patch,
            "log": log,
            "check_fast_log": self.check_fast_log,
            "check_full_log": self.check_full_log,
        }

    def check_fast(self, skip_build=False):
        """快速检查：build + verify_test_group，不跑 llvm-lit。"""
        with TimeCompensationGuard(self):
            self.fast_check_count += 1
            if not skip_build:
                res, reason = self.build()
                if not res:
                    return (False, reason)
            else:
                self.build_count += 1
            res, log = llvm_helper.verify_test_group(
                repro=False, input=self.data["tests"], type=self.bug_type
            )
            if not res:
                return (False, log)
            self.fast_check_pass = True
            return (True, log)

    def check_full(self):
        """完整检查：build + verify_test_group + verify_lit。"""
        with TimeCompensationGuard(self):
            self.full_check_count += 1
            res, reason = self.build()
            if not res:
                return (False, reason)
            res, log = llvm_helper.verify_test_group(
                repro=False, input=self.data["tests"], type=self.bug_type
            )
            self.check_fast_log.append(log)
            if not res:
                return (False, log)
            self.fast_check_pass = True
            res, log = llvm_helper.verify_lit(
                test_commit=self.test_commit,
                dirs=self.data["lit_test_dir"],
                max_test_jobs=self.max_build_jobs,
            )
            self.check_full_log.append(log)
            if not res:
                return (False, log)
            self.full_check_pass = True
            return (True, log)

    def get_bug_type(self):
        """返回样本 bug_type。"""
        return self.bug_type

    def get_base_commit(self):
        """返回样本 base_commit。"""
        return self.base_commit

    def get_tests(self):
        """返回样本 tests 列表。"""
        return self.data["tests"]

    def get_hint_fix_commit(self):
        """获取 hint:fix_commit，并登记知识使用。"""
        self.use_knowledge("hint:fix_commit", self.knowledge_cutoff)
        return self.data["hints"].get("fix_commit")

    def get_hint_components(self):
        # self.use_knowledge("hint:components", self.knowledge_cutoff)
        return self.data["hints"].get("components")

    def get_hint_files(self):
        """获取行级定位中的文件列表。"""
        self.use_knowledge("hint:files", self.knowledge_cutoff)
        lineno = self.data["hints"].get("bug_location_lineno")
        if lineno is None:
            return None
        return sorted(lineno.keys())

    def get_hint_bug_functions(self):
        """获取函数级 bug 位置提示。"""
        self.use_knowledge("hint:bug_functions", self.knowledge_cutoff)
        return self.data["hints"].get("bug_location_funcname")

    def get_hint_line_level_bug_locations(self):
        """获取行级 bug 位置提示。"""
        self.use_knowledge("hint:line_level_bug_locations", self.knowledge_cutoff)
        return self.data["hints"].get("bug_location_lineno")

    def get_hint_issue(self):
        """获取 issue 标题/正文/评论等原始上下文。"""
        self.use_knowledge("hint:issue", self.knowledge_cutoff)
        return self.data.get("issue")

    def get_ir_keywords(self, ir: str):
        """从 IR 文本中抽取指令名与 intrinsic 名称。"""
        keywords = set()
        # instructions
        instruction_pattern = re.compile(r"%.+ = (\w+) ")
        for match in re.findall(instruction_pattern, ir):
            keywords.add(match)
        # intrinsics
        intrinsic_pattern = re.compile(r"@(llvm.\w+)\(")
        for match in re.findall(intrinsic_pattern, ir):
            keywords.add(match)
        keywords.discard("call")
        return keywords

    def get_langref_desc(self, keywords):
        """读取 LangRef 中对应关键字片段，作为解释性知识。"""
        self.use_knowledge("llvm/docs/LangRef.rst", self.knowledge_cutoff)
        return llvm_helper.get_langref_desc(keywords, self.base_commit)

    # NOTE: It is not a hint.
    def is_single_func_fix(self):
        """样本是否单函数修复（来自 properties）。"""
        self.use_knowledge("is_single_func_fix", self.knowledge_cutoff)
        return self.data.get("properties").get("is_single_func_fix")

    # NOTE: It is not a hint.
    def is_single_file_fix(self):
        """样本是否单文件修复（来自 properties）。"""
        self.use_knowledge("is_single_file_fix", self.knowledge_cutoff)
        return self.data.get("properties").get("is_single_file_fix")

    # NOTE: It is not a hint.
    def get_bisect_commit(self) -> Optional[str]:
        """获取可解析的 bisect 结果提交；无效时返回 None。"""
        bisect_commit = self.data.get("bisect")
        if not bisect_commit:
            return None
        run = subprocess.run(
            ["git", "-C", llvm_helper.llvm_dir, "rev-parse", bisect_commit],
            capture_output=True,
        )
        if run.returncode != 0:
            return None
        if run.stdout.decode().strip() != bisect_commit:
            return None
        self.use_knowledge("bisect", self.knowledge_cutoff)
        return bisect_commit
