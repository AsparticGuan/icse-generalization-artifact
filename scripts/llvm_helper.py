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
import subprocess
import re
import tempfile

# NOTE: llvm-lit requires psutil
import psutil  # noqa: F401

llvm_dir = os.environ["LAB_LLVM_DIR"]
llvm_build_dir = os.environ["LAB_LLVM_BUILD_DIR"]
llvm_alive_tv = os.environ["LAB_LLVM_ALIVE_TV"]
llvm_cost_tool = os.environ["LAB_LLVM_COST_TOOL"]
dataset_dir = os.environ["LAB_DATASET_DIR"]
NINJA_QUIET = ["--", "--quiet"]
if "--quiet" not in subprocess.run(
    ["ninja", "--help"], capture_output=True
).stderr.decode("utf-8"):
    NINJA_QUIET = []


def git_execute(args):
    return subprocess.check_output(
        ["git", "-C", llvm_dir] + args, cwd=llvm_dir, stderr=subprocess.DEVNULL
    ).decode("utf-8")


def reset(commit):
    git_execute(["restore", "--staged", "."])
    git_execute(["clean", "-fdx"])
    git_execute(["checkout", "."])
    git_execute(["checkout", commit])


def infer_related_components(diff_files):
    prefixes = [
        "llvm/lib/Analysis/",
        "llvm/lib/Transforms/Scalar/",
        "llvm/lib/Transforms/Vectorize/",
        "llvm/lib/Transforms/Utils/",
        "llvm/lib/Transforms/IPO/",
        "llvm/lib/Transforms/",
        "llvm/lib/IR/",
    ]
    components = set()
    for file in diff_files:
        for prefix in prefixes:
            if file.startswith(prefix):
                component_name = (
                    file.removeprefix(prefix)
                    .split("/")[0]
                    .removesuffix(".cpp")
                    .removesuffix(".h")
                )
                if component_name != "":
                    if (
                        component_name.startswith("VPlan")
                        or component_name.startswith("LoopVectoriz")
                        or component_name.startswith("VPRecipe")
                    ):
                        component_name = "LoopVectorize"
                    if component_name.startswith("ScalarEvolution"):
                        component_name = "ScalarEvolution"
                    if component_name.startswith("ConstantFold"):
                        component_name = "ConstantFold"
                    if "AliasAnalysis" in component_name:
                        component_name = "AliasAnalysis"
                    if component_name.startswith("Attributor"):
                        component_name = "Attributor"
                    if file.startswith("llvm/lib/IR"):
                        component_name = "IR"
                    components.add(component_name)
                    break
    return components


def get_langref_desc(keywords, commit):
    langref = str(git_execute(["show", f"{commit}:llvm/docs/LangRef.rst"]))
    desc = dict()
    sep1 = ".. _"
    sep2 = "\n^^^"
    for keyword in keywords:
        matched = re.search(f"\n'``{keyword}.+\n\\^", langref)
        if matched is None:
            continue
        beg, end = matched.span()
        beg = langref.rfind(sep1, None, beg)
        end1 = langref.find(sep2, end)
        end2 = langref.rfind(sep1, None, end1)
        desc[keyword] = langref[beg:end2]
    return desc


def decode_output(output):
    if output is None:
        return ""
    return output.decode()


def build(max_build_jobs: int, additional_cmake_args=[]):
    os.makedirs(llvm_build_dir, exist_ok=True)
    log = ""
    try:
        log += subprocess.check_output(
            [
                "cmake",
                "-S",
                llvm_dir + "/llvm",
                "-G",
                "Ninja",
                "-DBUILD_SHARED_LIBS=ON",
                "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
                # "-DCMAKE_C_COMPILER_LAUNCHER=ccache",
                # "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
                "-DLLVM_ENABLE_ASSERTIONS=ON",
                "-DLLVM_ABI_BREAKING_CHECKS=WITH_ASSERTS",
                "-DLLVM_ENABLE_WARNINGS=OFF",
                "-DLLVM_APPEND_VC_REV=OFF",
                "-DLLVM_TARGETS_TO_BUILD='X86;RISCV;AArch64;SystemZ;Hexagon;PowerPC;'",
                "-DLLVM_PARALLEL_LINK_JOBS=4",
                "-DLLVM_INCLUDE_EXAMPLES=OFF",
                "-DLLVM_ENABLE_ZSTD=OFF",
            ]
            + additional_cmake_args,
            stderr=subprocess.STDOUT,
            cwd=llvm_build_dir,
        ).decode()
        log += subprocess.check_output(
            ["cmake", "--build", ".", "-j", str(max_build_jobs)] + NINJA_QUIET,
            stderr=subprocess.STDOUT,
            cwd=llvm_build_dir,
        ).decode()
        return (True, log)
    except subprocess.CalledProcessError as e:
        return (False, log + "\n" + decode_output(e.output))


def is_valid_comment(comment):
    if comment["author"] == "llvmbot":
        return False
    if comment["body"].startswith("/cherry-pick"):
        return False
    return True


def apply(patch: str):
    try:
        out = subprocess.check_output(
            ["git", "-C", llvm_dir, "apply"],
            cwd=llvm_dir,
            stderr=subprocess.STDOUT,
            input=patch.encode(),
        ).decode("utf-8")
        return (True, out)
    except subprocess.CalledProcessError as e:
        return (False, str(e) + "\n" + decode_output(e.output))


def filter_out_unsupported_feats(src: str):
    src = src.replace(" noalias ", " ")
    src = src.replace(" nofree ", " ")
    return src


def alive2_check(src: str, tgt: str, additional_args: str):
    try:
        with tempfile.NamedTemporaryFile() as src_file:
            with tempfile.NamedTemporaryFile() as tgt_file:
                src = filter_out_unsupported_feats(src)
                tgt = filter_out_unsupported_feats(tgt)
                src_file.write(src.encode())
                tgt_file.write(tgt.encode())
                src_file.flush()
                tgt_file.flush()

                args = [
                    llvm_alive_tv,
                    "--disable-undef-input",
                    src_file.name,
                    tgt_file.name,
                ]
                if additional_args is not None:
                    args += additional_args.strip().split(" ")

                out = subprocess.check_output(args, stderr=subprocess.STDOUT).decode()
                success = (
                    "0 incorrect transformations" in out
                    and "0 failed-to-prove transformations" in out
                    and "0 Alive2 errors" in out
                )
                return (success, {"src": src, "tgt": tgt, "log": out})
    except subprocess.CalledProcessError as e:
        return (False, str(e) + "\n" + decode_output(e.output))


def lli_check(tgt: bytes, expected_out: str):
    try:
        out = subprocess.check_output(
            [os.path.join(llvm_build_dir, "bin/lli")],
            input=tgt,
            timeout=10.0,
            stderr=subprocess.STDOUT,
        ).decode()
        if out == expected_out:
            return (True, "success")
        return (False, f"Expected '{expected_out}', but got '{out}'")
    except subprocess.CalledProcessError as e:
        return (False, str(e) + "\n" + decode_output(e.output))
    except subprocess.TimeoutExpired as e:
        return (False, str(e) + "\n" + decode_output(e.output))

def cost_check(source_program: str, expect_optimized_program: str, current_optimized_program: str):
    """
    Check the cost of the current optimized program compared to the expect optimized program.
    """
    cost_command = []
    programs = {
        "source_program": source_program,
        "expect_optimized_program": expect_optimized_program,
        "current_optimized_program": current_optimized_program
    }
    costs = {
        "source_program": -1,
        "expect_optimized_program": -1,
        "current_optimized_program": -1
    }
    for prog_name in programs:
        with tempfile.NamedTemporaryFile(suffix=".ll") as code_file:
            code_file.write(programs[prog_name].encode())
            code_file.flush()
            try:
                out = subprocess.check_output([llvm_cost_tool, code_file.name], stderr=subprocess.STDOUT).decode()
                cost = int(out.strip().split("Cost: ")[1])
                costs[prog_name] = cost
            except subprocess.CalledProcessError as e:
                print(e)
                pass
    success = False
    # Should be modified to if test["cost"]["current_optimized_program"] < test["cost"]["source_program"] or 
    # \ test["cost"]["current_optimized_program"] <= test["cost"]["expect_optimized_program"]:
    # if costs["current_optimized_program"] < costs["source_program"] and \
    #     costs["current_optimized_program"] <= costs["expect_optimized_program"]:
    if costs["current_optimized_program"] < costs["source_program"] and \
        costs["current_optimized_program"] <= costs["expect_optimized_program"]:
        success = True

    print(f"costs: {costs}")
    print(f"success: {success}")
    return (success, costs)

def filecheck_check(src: str, tgt: str, args_list: list[str]):
    filecheck_command = []
    for arg in args_list:
        if '--check-prefix' not in arg:
            filecheck_command.append(arg)
        else:
            check_prefixes_raw = re.findall(r"check-prefixes=(.*)", arg) + re.findall(r"check-prefix=(.*)", arg)
            check_prefixes_used = []
            if check_prefixes_raw:
                check_prefixes = check_prefixes_raw[0].strip().split(",")
                for check_prefix in check_prefixes:
                    if f"; {check_prefix}" in src:
                        check_prefixes_used.append(check_prefix)
            if len(check_prefixes_used) != 0:
                filecheck_command.append(f"--check-prefixes={','.join(check_prefixes_used)}")
    with tempfile.NamedTemporaryFile() as src_file:
        with tempfile.NamedTemporaryFile() as tgt_file:
            src = filter_out_unsupported_feats(src)
            tgt = filter_out_unsupported_feats(tgt)
            src_file.write(src.encode())
            tgt_file.write(tgt.encode())
            src_file.flush()
            tgt_file.flush()
            filecheck_command.append(src_file.name)
            try:
                # Create a pipe to feed tgt_file content as input
                with open(tgt_file.name, 'rb') as tgt_input:
                    out = subprocess.check_output(filecheck_command, stdin=tgt_input, stderr=subprocess.STDOUT).decode()
                success = len(out.strip()) == 0
                return (success, {"src": src, "tgt": tgt, "log": out})
            except subprocess.CalledProcessError as e:
                return (False, {"src": src, "tgt": tgt, "log": str(e) + "\n" + decode_output(e.output)})

def copy_triple(input: str, out: bytes):
    triple_pattern = "target triple ="
    if triple_pattern in input:
        return input
    ref_out = out.decode()
    if triple_pattern in ref_out:
        triple_pos = ref_out.find(triple_pattern)
        triple_line = ref_out[triple_pos : ref_out.find("\n", triple_pos) + 1]
        return triple_line + input
    return input


def copy_datalayout(input: str, out: bytes):
    datalayout_pattern = "target datalayout ="
    if datalayout_pattern in input:
        return input
    ref_out = out.decode()
    if datalayout_pattern in ref_out:
        datalayout_pos = ref_out.find(datalayout_pattern)
        datalayout_line = ref_out[
            datalayout_pos : ref_out.find("\n", datalayout_pos) + 1
        ]
        return datalayout_line + input
    return input


def verify_dispatch(
    repro: bool,
    input: str,
    commands: list[str],
    type: str,
    additional_args: str,
    lli_expected_out: str,
    source_program: str = None,
    expect_optimized_program: str = None,
):
    run_args_list = list(
        filter(
            lambda x: x != "",
            commands[0].replace("< ", " ")
            .replace("%s", "-")
            .replace("2>&1", "")
            .replace("'", "")
            .replace('"', "")
            .replace("opt", os.path.join(llvm_build_dir, "bin/opt"), 1)
            .strip()
            .split(" "),
        )
    )
    filecheck_args_list = list(
        filter(
            lambda x: x != "",
            commands[1].replace("< ", " ")
            .replace("%s", "")
            .replace("2>&1", "")
            .replace("'", "")
            .replace('"', "")
            .replace("FileCheck", os.path.join(llvm_build_dir, "bin/FileCheck"), 1)
            .strip()
            .split(" "),
        )
    )
    try:
        out = subprocess.run(
            run_args_list,
            input=input.encode(),
            timeout=120.0 if type == "crash" else 10.0,
            check=True,
            capture_output=True,
        )
        if type == "missed-optimization":
            output = out.stdout
            new_input = copy_triple(input, output)
            new_input = copy_datalayout(new_input, output)
            # we don't care about the alive2 result here because filecheck gives a stricter check
            res_alive2, log_alive2 = alive2_check(new_input, output.decode(), additional_args)
            if source_program is not None and expect_optimized_program is not None:
                current_optimized_program = output.decode().removeprefix(
                    "; ModuleID = '<stdin>'\nsource_filename = \"<stdin>\"\n"
                    ).removeprefix("\n")
                res_cost, log_cost = cost_check(source_program, expect_optimized_program, current_optimized_program)
            else:
                res_cost = False
                log_cost = ""
            res_filecheck, log_filecheck = filecheck_check(new_input, output.decode(), filecheck_args_list)
            log = {"alive2": log_alive2, "cost": log_cost, "filecheck": log_filecheck}
            if source_program is not None and expect_optimized_program is not None:
                log["source_program"] = source_program
                log["expect_optimized_program"] = expect_optimized_program
                log["current_optimized_program"] = current_optimized_program
            if repro:
                res = not res_filecheck
            else:
                res = res_filecheck
                if not res:
                    # if filecheck fails, we use alive2_check and cost_check to check if the optimized program is correct and cost is low
                    res = res_alive2 and res_cost
            log["opt_stderr"] = decode_output(out.stderr)
            return (res, log)
        return (not repro, "success\n" + decode_output(out.stderr))
    except Exception as e:
        return (
            False,
            str(e) + "\n" + decode_output(e.output) + "\n" + decode_output(e.stderr),
        )

def verify_test_group(repro: bool, input, type: str):
    test_res = []
    overall_test_res = not repro
    for test in input:
        print("test: ", test)
        file = test["file"]
        commands = test["commands"]
        tests = test["tests"]
        for subtest in tests:
            name = subtest["test_name"]
            body = subtest["test_body"]
            for args in commands:
                res, log = verify_dispatch(
                    repro,
                    body,
                    args,
                    type,
                    subtest.get("additional_args"),
                    subtest.get("lli_expected_out"),
                    subtest.get("source_program"),
                    subtest.get("expect_optimized_program"),
                )
                test_res.append(
                    {
                        "file": file,
                        "args": args,
                        "name": name,
                        "body": body,
                        "result": res,
                        "log": log,
                    }
                )
                if repro:
                    overall_test_res = overall_test_res or res
                else:
                    overall_test_res = overall_test_res and res
    return (overall_test_res, test_res)


# Component name -> opt pass(es) for dataset-orig (no FileCheck, test_body empty)
_COMPONENT_TO_OPT_PASS = {
    "InstCombine": "instcombine",
    "Scalar": "default",  # generic scalar opts
}


def verify_test_group_orig(repro: bool, input_tests, type_str: str, component_list):
    """
    Verify tests when test_body is empty and commands is empty (e.g. dataset-orig).
    Runs opt on source_program with pass inferred from component, then checks
    alive2 + cost only (no FileCheck).
    """
    test_res = []
    overall_test_res = not repro
    if not component_list:
        component_list = ["InstCombine"]
    component = component_list[0] if component_list else "InstCombine"
    pass_name = _COMPONENT_TO_OPT_PASS.get(component, "instcombine")
    opt_base = [os.path.join(llvm_build_dir, "bin/opt"), "--passes=" + pass_name, "-S"]

    for test in input_tests:
        print("test: ", test)
        file = test["file"]
        commands = test.get("commands", [])
        tests = test["tests"]
        if commands:
            # Fallback to normal verify if commands present
            for subtest in tests:
                body = subtest.get("test_body", "")
                for args in commands:
                    res, log = verify_dispatch(
                        repro,
                        body,
                        args,
                        type_str,
                        subtest.get("additional_args"),
                        subtest.get("lli_expected_out"),
                        subtest.get("source_program"),
                        subtest.get("expect_optimized_program"),
                    )
                    test_res.append(
                        {
                            "file": file,
                            "args": args,
                            "name": subtest["test_name"],
                            "body": body,
                            "result": res,
                            "log": log,
                        }
                    )
                    if repro:
                        overall_test_res = overall_test_res or res
                    else:
                        overall_test_res = overall_test_res and res
            continue

        # commands empty: run opt on source_program, check alive2 + cost only (no FileCheck)
        for subtest in tests:
            name = subtest["test_name"]
            source_program = subtest.get("source_program") or ""
            expect_optimized_program = subtest.get("expect_optimized_program") or ""
            if not source_program or not expect_optimized_program:
                test_res.append(
                    {
                        "file": file,
                        "args": "(no FileCheck)",
                        "name": name,
                        "body": "",
                        "result": False,
                        "log": {"error": "missing source_program or expect_optimized_program"},
                    }
                )
                overall_test_res = False
                continue
            try:
                out = subprocess.run(
                    opt_base,
                    input=source_program.encode(),
                    timeout=10.0,
                    capture_output=True,
                    check=True,
                )
                output = out.stdout.decode()
                new_input = copy_triple(source_program, out.stdout)
                new_input = copy_datalayout(new_input, out.stdout)
                additional_args = subtest.get("additional_args")
                res_alive2, log_alive2 = alive2_check(
                    new_input, output, additional_args
                )
                current_optimized_program = output.removeprefix(
                    "; ModuleID = '<stdin>'\nsource_filename = \"<stdin>\"\n"
                ).removeprefix("\n")
                res_cost, log_cost = cost_check(
                    source_program, expect_optimized_program, current_optimized_program
                )
                log = {
                    "alive2": log_alive2,
                    "cost": log_cost,
                    "source_program": source_program,
                    "expect_optimized_program": expect_optimized_program,
                    "current_optimized_program": current_optimized_program,
                }
                res = res_alive2 and res_cost
                print(f"res: {res}")
                print(f"res_alive2: {res_alive2}")
                print(f"res_cost: {res_cost}")
                test_res.append(
                    {
                        "file": file,
                        "args": "(no FileCheck)",
                        "name": name,
                        "body": "",
                        "result": res,
                        "log": log,
                    }
                )
                if repro:
                    overall_test_res = overall_test_res or res
                else:
                    overall_test_res = overall_test_res and res
            except Exception as e:
                err_log = str(e)
                if hasattr(e, "stderr") and e.stderr:
                    err_log += "\n" + decode_output(e.stderr)
                if hasattr(e, "stdout") and e.stdout:
                    err_log += "\n" + decode_output(e.stdout)
                test_res.append(
                    {
                        "file": file,
                        "args": "(no FileCheck)",
                        "name": name,
                        "body": "",
                        "result": False,
                        "log": {"error": err_log},
                    }
                )
                overall_test_res = False
    return (overall_test_res, test_res)


def verify_lit(test_commit, dirs, max_test_jobs):
    try:
        git_execute(["checkout", test_commit, "llvm/test"])
        test_dirs = [os.path.join(llvm_dir, x) for x in dirs]
        out = subprocess.check_output(
            [
                os.path.join(llvm_build_dir, "bin/llvm-lit"),
                "--no-progress-bar",
                "-j",
                str(max_test_jobs),
                "--max-failures",
                "1",
                "--order",
                "lexical",
                "-sv",
            ]
            + test_dirs,
            stderr=subprocess.STDOUT,
            timeout=300.0,
        ).decode()
        return (True, out)
    except subprocess.CalledProcessError as e:
        return (False, str(e) + "\n" + decode_output(e.output))
    except subprocess.TimeoutExpired as e:
        return (False, str(e) + "\n" + decode_output(e.output))


def get_first_failed_test(test_result):
    for res in test_result:
        if not res["result"]:
            return res
    return None


def is_valid_fix(commit):
    if commit is None:
        return False
    try:
        branches = git_execute(["branch", "--contains", commit])
        if "main\n" not in branches:
            return False
        changed_files = (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    llvm_dir,
                    "show",
                    "--name-only",
                    "--format=",
                    commit,
                ],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        if "llvm/test/" in changed_files and (
            "llvm/lib/" in changed_files or "llvm/include/" in changed_files
        ):
            return True
    except subprocess.CalledProcessError:
        pass
    return False
