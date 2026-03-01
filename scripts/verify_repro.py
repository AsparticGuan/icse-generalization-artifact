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

import os
import sys
import llvm_helper
import json
import re

max_build_jobs = os.cpu_count()


def verify_issue(issue):
    path = os.path.join(llvm_helper.dataset_dir, issue)
    with open(path) as f:
        data = json.load(f)
    if data.get("verified", False):
        return
    # print(data["issue"]["title"])
    base_commit = data["base_commit"]
    llvm_helper.reset(base_commit)
    print("Stage 1 build")
    res, log = llvm_helper.build(max_build_jobs)
    if not res:
        # print(log)
        raise RuntimeError("Failed to build")
    bug_type = data["bug_type"]
    print("Stage 1 verify")
    # res, log = llvm_helper.verify_test_group(
    #     repro=True, input=data["tests"], type=bug_type
    # )
    res, log = llvm_helper.verify_test_group_orig(
        repro=True, input_tests=data["tests"], type_str=bug_type, component_list=data["hints"].get("components")
    )
    print(json.dumps(log, indent=2))
    print(res)
    if not res:
        raise RuntimeError("Failed to reproduce")
    else:
        # we successfully reproduced the bug, so dump the failed_to_optimize programs for later use
        for log_index in range(len(log)):
            for test_file_index in range(len(data["tests"])):
                if data["tests"][test_file_index]["file"] != log[log_index]["file"]:
                    continue
                for test_index in range(len(data["tests"][test_file_index]["tests"])):
                    if data["tests"][test_file_index]["tests"][test_index]["test_name"] != log[log_index]["name"]:
                        continue
                    # optimized_program = log[log_index]["log"]["filecheck"]["tgt"]
                    optimized_program = log[log_index]["log"]["alive2"]["tgt"]
                    optimized_program = optimized_program.removeprefix(
                        "; ModuleID = '<stdin>'\nsource_filename = \"<stdin>\"\n"
                    ).removeprefix("\n")
                    optimized_program = optimized_program.removesuffix("\n\n; Function Attrs: nocallback nosync nounwind speculatable willreturn memory(none)\ndeclare i8 @llvm.umax.i8(i8, i8) #0\n\nattributes #0 = { nocallback nosync nounwind speculatable willreturn memory(none) }\n")
                    data["tests"][test_file_index]["tests"][test_index]["current_optimized_program"] = optimized_program
                    break
                break
            assert re.findall(f'@{log[log_index]["name"]}\(', optimized_program) != [], f"optimized_program: {optimized_program}"
            
    llvm_helper.apply(data["patch"])
    print("Stage 2 build")
    res, log = llvm_helper.build(max_build_jobs)
    if not res:
        # print(log)
        raise RuntimeError("Failed to build")
    print("Stage 2 verify")
    res, log = llvm_helper.verify_test_group(
        repro=False, input=data["tests"], type=bug_type
    )
    if not res:
        print(json.dumps(llvm_helper.get_first_failed_test(log), indent=2))
        raise RuntimeError("Failed to fix")
    else:
        # we successfully reproduced the bug, so dump the expected_to_optimize programs for later use
        for log_index in range(len(log)):
            for test_file_index in range(len(data["tests"])):
                if data["tests"][test_file_index]["file"] != log[log_index]["file"]:
                    continue
                for test_index in range(len(data["tests"][test_file_index]["tests"])):
                    if data["tests"][test_file_index]["tests"][test_index]["test_name"] != log[log_index]["name"]:
                        continue
                    optimized_program = log[log_index]["log"]["filecheck"]["tgt"]
                    optimized_program = optimized_program.removeprefix(
                        "; ModuleID = '<stdin>'\nsource_filename = \"<stdin>\"\n"
                    ).removeprefix("\n")
                    data["tests"][test_file_index]["tests"][test_index]["expect_optimized_program"] = optimized_program
                    break
                break
            assert re.findall(f'@{log[log_index]["name"]}\(', optimized_program) != []

    print("Stage 2 lit check")
    res, log = llvm_helper.verify_lit(
        test_commit=data.get("test_commit", data["hints"]["fix_commit"]),
        dirs=data["lit_test_dir"],
        max_test_jobs=max_build_jobs,
    )
    if not res:
        print(log)
        raise RuntimeError("Lit check failure")
    data["verified"] = True

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


task_list = []
if len(sys.argv) == 2:
    task_list = [sys.argv[1] + ".json"]
else:
    for name in os.listdir(llvm_helper.dataset_dir):
        if name.endswith(".json"):
            task_list.append(name)
task_list.sort()

for idx, task in enumerate(task_list):
    print("Verifying", idx + 1, task.removesuffix(".json"))
    verify_issue(task)
    # try:
    #     verify_issue(task)
    #     print("Verification successful")
    # except Exception as e:
    #     print(e)
    #     print("Verification failed")
