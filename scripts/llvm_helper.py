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

"""LLVM 实验脚本通用辅助库。

本模块集中封装了脚本层最核心的基础能力：
1. Git 仓库操作（reset、show、patch 应用等）；
2. LLVM 构建与测试调度（cmake/ninja、llvm-lit）；
3. 语义正确性检查（Alive2、FileCheck、lli）；
4. 样本验证流程中复用的数据清洗和结果判定工具。

scripts/ 下大多数脚本都会直接 import 本文件，因此它相当于“运行时基座”。
"""

import os
import subprocess
import re
import tempfile
from pathlib import Path

# NOTE: llvm-lit requires psutil
import psutil  # noqa: F401

# 运行环境所需路径/工具全部由外部环境变量注入。
llvm_dir = os.environ["LAB_LLVM_DIR"]
llvm_build_dir = os.environ["LAB_LLVM_BUILD_DIR"]
llvm_alive_tv = os.environ["LAB_LLVM_ALIVE_TV"]
llvm_cost_tool = os.environ["LAB_LLVM_COST_TOOL"]
dataset_dir = os.environ["LAB_DATASET_DIR"]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ALIVE2_HEALTHCHECK_SAMPLE = (
    _PROJECT_ROOT
    / "utils"
    / "alive2"
    / "tests"
    / "alive-tv"
    / "noundef_metadata.srctgt.ll"
)
_ALIVE2_HEALTHCHECK_RESULT = None
_DEFAULT_TMP_DIR = str(_PROJECT_ROOT / ".tmp")

# 兼容不同 Ninja 版本：若支持 --quiet，则构建时默认静默输出。
NINJA_QUIET = ["--", "--quiet"]
if "--quiet" not in subprocess.run(
    ["ninja", "--help"], capture_output=True
).stderr.decode("utf-8"):
    NINJA_QUIET = []


def _tmp_dir_from_env():
    """返回并校验临时目录；未配置时回退到项目内 .tmp。"""
    tmp_dir = (
        os.environ.get("LAB_TMP_DIR") or os.environ.get("TMPDIR") or _DEFAULT_TMP_DIR
    )
    os.makedirs(tmp_dir, exist_ok=True)
    if not os.access(tmp_dir, os.W_OK | os.X_OK):
        raise PermissionError(f"Temporary directory is not writable: {tmp_dir}")
    os.environ["LAB_TMP_DIR"] = tmp_dir
    os.environ["TMPDIR"] = tmp_dir
    return tmp_dir


def _named_temp_file(**kwargs):
    """统一临时文件创建入口，优先使用 LAB_TMP_DIR/TMPDIR。"""
    tmp_dir = _tmp_dir_from_env()
    if tmp_dir:
        kwargs.setdefault("dir", tmp_dir)
    return tempfile.NamedTemporaryFile(**kwargs)


def _alive2_summary_success(output: str) -> bool:
    """Alive2 成功判定（兼容 summary 与简化输出）。"""
    if "Transformation seems to be correct" in output:
        return True
    return (
        "0 incorrect transformations" in output
        and "0 failed-to-prove transformations" in output
        and "0 Alive2 errors" in output
    )


def _detect_alive2_infra_reason(output: str):
    """识别 Alive2 工具链/运行时故障，返回 infra reason 或 None。"""
    text = str(output or "")
    lower = text.lower()
    if "permission denied" in lower and "/tmp" in lower:
        return "tmp_permission_denied"
    if "error while loading shared libraries" in lower:
        return "alive2_missing_runtime"
    if "no such file or directory" in lower and "alive" in lower:
        return "alive2_binary_missing"
    if "not executable" in lower and "alive" in lower:
        return "alive2_not_executable"
    if "block cannot be empty" in lower:
        return "alive2_parse_error"
    if "usage: alive2" in lower:
        return "alive2_wrong_binary"
    return None


def _build_alive2_infra_log(reason: str, detail: str):
    """构造统一 infra 错误日志结构。"""
    return {
        "infra_error": True,
        "infra_reason": reason,
        "log": detail,
    }


def _alive2_log_is_infra(log):
    return isinstance(log, dict) and bool(log.get("infra_error"))


def _test_log_has_infra_error(log):
    if not isinstance(log, dict):
        return False
    if log.get("infra_error"):
        return True
    return _alive2_log_is_infra(log.get("alive2"))


def _alive2_healthcheck():
    """对 alive-tv 做一次性健康检查，避免后续反复失败。"""
    global llvm_alive_tv
    global _ALIVE2_HEALTHCHECK_RESULT
    if _ALIVE2_HEALTHCHECK_RESULT is not None:
        return _ALIVE2_HEALTHCHECK_RESULT

    project_alive_tv = str(_PROJECT_ROOT / "utils" / "alive2" / "build" / "alive-tv")
    candidates = []
    for cand in [
        llvm_alive_tv,
        "/usr/local/bin/alive-tv",
        "/usr/bin/alive-tv",
        project_alive_tv,
    ]:
        if cand and cand not in candidates:
            candidates.append(cand)

    last_error = ""
    for cand in candidates:
        if not os.path.isfile(cand):
            last_error = f"alive-tv not found: {cand}"
            continue
        if not os.access(cand, os.X_OK):
            last_error = f"alive-tv is not executable: {cand}"
            continue
        if os.path.basename(os.path.realpath(cand)) == "alive":
            last_error = (
                f"alive-tv points to alive binary: {cand} -> {os.path.realpath(cand)}"
            )
            continue

        if _ALIVE2_HEALTHCHECK_SAMPLE.is_file():
            try:
                out = subprocess.run(
                    [cand, str(_ALIVE2_HEALTHCHECK_SAMPLE)],
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                    check=False,
                )
                merged = (out.stdout or "") + (f"\n{out.stderr}" if out.stderr else "")
                if _alive2_summary_success(merged):
                    llvm_alive_tv = cand
                    os.environ["LAB_LLVM_ALIVE_TV"] = cand
                    _ALIVE2_HEALTHCHECK_RESULT = (True, f"ok:{cand}")
                    return _ALIVE2_HEALTHCHECK_RESULT
                reason = _detect_alive2_infra_reason(merged)
                if reason is not None:
                    last_error = (
                        f"alive-tv healthcheck failed for {cand} ({reason}):\n{merged}"
                    )
                    continue
                if out.returncode != 0:
                    last_error = f"alive-tv healthcheck failed for {cand} with code {out.returncode}:\n{merged}"
                    continue
                llvm_alive_tv = cand
                os.environ["LAB_LLVM_ALIVE_TV"] = cand
                _ALIVE2_HEALTHCHECK_RESULT = (True, f"ok:{cand}")
                return _ALIVE2_HEALTHCHECK_RESULT
            except Exception as e:
                last_error = f"alive-tv healthcheck exception for {cand}: {e}"
                continue
        else:
            llvm_alive_tv = cand
            os.environ["LAB_LLVM_ALIVE_TV"] = cand
            _ALIVE2_HEALTHCHECK_RESULT = (True, f"ok:{cand}")
            return _ALIVE2_HEALTHCHECK_RESULT

    _ALIVE2_HEALTHCHECK_RESULT = (False, last_error or "alive-tv healthcheck failed")
    return _ALIVE2_HEALTHCHECK_RESULT


def git_execute(args):
    """在 llvm_dir 上执行 git 子命令并返回 UTF-8 文本输出。"""
    return subprocess.check_output(
        ["git", "-C", llvm_dir] + args, cwd=llvm_dir, stderr=subprocess.DEVNULL
    ).decode("utf-8")


def reset(commit):
    """将 LLVM 工作树强制重置到指定提交。"""
    git_execute(["restore", "--staged", "."])
    git_execute(["clean", "-fdx"])
    git_execute(["checkout", "."])
    git_execute(["checkout", commit])


def infer_related_components(diff_files):
    """根据改动文件路径推断 LLVM 组件名集合。"""
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
                    # 一组规则化映射：把同族文件名归并到稳定组件名。
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
    """从指定提交的 LangRef.rst 中抽取关键词对应章节片段。"""
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
    """安全地把 subprocess bytes 输出解码为字符串。"""
    if output is None:
        return ""
    return output.decode()


def build(max_build_jobs: int, additional_cmake_args=[]):
    """配置并构建 LLVM，返回 (是否成功, 构建日志)。"""
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
        # CMake 配置成功后再执行真实编译阶段。
        log += subprocess.check_output(
            ["cmake", "--build", ".", "-j", str(max_build_jobs)] + NINJA_QUIET,
            stderr=subprocess.STDOUT,
            cwd=llvm_build_dir,
        ).decode()
        return (True, log)
    except subprocess.CalledProcessError as e:
        return (False, log + "\n" + decode_output(e.output))


def is_valid_comment(comment):
    """过滤 issue 评论中的噪声内容。"""
    if comment["author"] == "llvmbot":
        return False
    if comment["body"].startswith("/cherry-pick"):
        return False
    return True


def apply(patch: str):
    """在 llvm_dir 上应用补丁文本，返回 (是否成功, 输出日志)。"""
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
    """移除 Alive2 暂不支持的 IR 属性，降低误报概率。"""
    src = src.replace(" noalias ", " ")
    src = src.replace(" nofree ", " ")
    return src


def alive2_check(src: str, tgt: str, additional_args: str):
    """调用 Alive2 对源码 IR 与优化后 IR 做语义等价检查。"""
    src = filter_out_unsupported_feats(src)
    tgt = filter_out_unsupported_feats(tgt)

    health_ok, health_log = _alive2_healthcheck()
    if not health_ok:
        return (
            False,
            {
                "src": src,
                "tgt": tgt,
                **_build_alive2_infra_log("alive2_healthcheck_failed", health_log),
            },
        )

    try:
        with _named_temp_file() as src_file:
            with _named_temp_file() as tgt_file:
                src_file.write(src.encode())
                tgt_file.write(tgt.encode())
                src_file.flush()
                tgt_file.flush()

                args = [
                    llvm_alive_tv,
                    "-disable-undef-input",
                    src_file.name,
                    tgt_file.name,
                ]
                if additional_args is not None:
                    args += [arg for arg in additional_args.strip().split(" ") if arg]

                out = subprocess.check_output(args, stderr=subprocess.STDOUT).decode()
                success = _alive2_summary_success(out)
                infra_reason = _detect_alive2_infra_reason(out)
                if infra_reason is not None:
                    return (
                        False,
                        {
                            "src": src,
                            "tgt": tgt,
                            **_build_alive2_infra_log(infra_reason, out),
                        },
                    )
                return (success, {"src": src, "tgt": tgt, "log": out})
    except subprocess.CalledProcessError as e:
        err_log = str(e) + "\n" + decode_output(e.output)
        infra_reason = _detect_alive2_infra_reason(err_log)
        if infra_reason is not None:
            return (
                False,
                {
                    "src": src,
                    "tgt": tgt,
                    **_build_alive2_infra_log(infra_reason, err_log),
                },
            )
        return (False, {"src": src, "tgt": tgt, "log": err_log})
    except Exception as e:
        # 例如 alive-tv 未安装或路径不存在时，返回失败日志而不是抛异常中断主流程。
        err_log = str(e)
        infra_reason = _detect_alive2_infra_reason(err_log) or "alive2_exception"
        return (
            False,
            {
                "src": src,
                "tgt": tgt,
                **_build_alive2_infra_log(infra_reason, err_log),
            },
        )


def lli_check(tgt: bytes, expected_out: str):
    """执行 lli 并比对程序输出，用于运行时行为验证。"""
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


def cost_check(
    source_program: str, expect_optimized_program: str, current_optimized_program: str
):
    """
    比较三份 IR 的代价估计：
    - source_program: 原始程序
    - expect_optimized_program: 修复提交上的期望优化结果
    - current_optimized_program: 当前待评估结果

    判定策略：current 需优于 source，且不劣于 expected。
    """
    cost_command = []
    programs = {
        "source_program": source_program,
        "expect_optimized_program": expect_optimized_program,
        "current_optimized_program": current_optimized_program,
    }
    costs = {
        "source_program": -1,
        "expect_optimized_program": -1,
        "current_optimized_program": -1,
    }
    for prog_name in programs:
        with _named_temp_file(suffix=".ll") as code_file:
            code_file.write(programs[prog_name].encode())
            code_file.flush()
            try:
                out = subprocess.check_output(
                    [llvm_cost_tool, code_file.name], stderr=subprocess.STDOUT
                ).decode()
                cost = int(out.strip().split("Cost: ")[1])
                costs[prog_name] = cost
            except Exception as e:
                print(e)
                pass
    success = False
    # Should be modified to if test["cost"]["current_optimized_program"] < test["cost"]["source_program"] or
    # \ test["cost"]["current_optimized_program"] <= test["cost"]["expect_optimized_program"]:
    # if costs["current_optimized_program"] < costs["source_program"] and \
    #     costs["current_optimized_program"] <= costs["expect_optimized_program"]:
    if (
        costs["current_optimized_program"] < costs["source_program"]
        and costs["current_optimized_program"] <= costs["expect_optimized_program"]
    ):
        success = True

    print(f"costs: {costs}")
    print(f"success: {success}")
    return (success, costs)


def filecheck_check(src: str, tgt: str, args_list: list[str]):
    """调用 FileCheck 验证优化结果是否满足测试断言。"""
    filecheck_command = []
    for arg in args_list:
        if "--check-prefix" not in arg:
            filecheck_command.append(arg)
        else:
            # 仅保留在 src 中真实出现的 check-prefix，避免误用无关前缀。
            check_prefixes_raw = re.findall(r"check-prefixes=(.*)", arg) + re.findall(
                r"check-prefix=(.*)", arg
            )
            check_prefixes_used = []
            if check_prefixes_raw:
                check_prefixes = check_prefixes_raw[0].strip().split(",")
                for check_prefix in check_prefixes:
                    if f"; {check_prefix}" in src:
                        check_prefixes_used.append(check_prefix)
            if len(check_prefixes_used) != 0:
                filecheck_command.append(
                    f"--check-prefixes={','.join(check_prefixes_used)}"
                )
    with _named_temp_file() as src_file:
        with _named_temp_file() as tgt_file:
            src = filter_out_unsupported_feats(src)
            tgt = filter_out_unsupported_feats(tgt)
            src_file.write(src.encode())
            tgt_file.write(tgt.encode())
            src_file.flush()
            tgt_file.flush()
            filecheck_command.append(src_file.name)
            try:
                # Create a pipe to feed tgt_file content as input
                with open(tgt_file.name, "rb") as tgt_input:
                    out = subprocess.check_output(
                        filecheck_command, stdin=tgt_input, stderr=subprocess.STDOUT
                    ).decode()
                success = len(out.strip()) == 0
                return (success, {"src": src, "tgt": tgt, "log": out})
            except subprocess.CalledProcessError as e:
                return (
                    False,
                    {
                        "src": src,
                        "tgt": tgt,
                        "log": str(e) + "\n" + decode_output(e.output),
                    },
                )


def copy_triple(input: str, out: bytes):
    """若 input 缺失 target triple，则从 out 中复制补齐。"""
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
    """若 input 缺失 target datalayout，则从 out 中复制补齐。"""
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
    """执行单条 RUN/FILECHECK 测试链并返回判定结果。

    - repro=True: 期望“复现失败行为”（filecheck 不通过才算成功复现）
    - repro=False: 期望“修复后通过验证”
    """
    # 解析并标准化 RUN 命令：替换 %s、stdin 重定向、opt 二进制路径等。
    run_args_list = list(
        filter(
            lambda x: x != "",
            commands[0]
            .replace("< ", " ")
            .replace("%s", "-")
            .replace("2>&1", "")
            .replace("'", "")
            .replace('"', "")
            .replace("opt", os.path.join(llvm_build_dir, "bin/opt"), 1)
            .strip()
            .split(" "),
        )
    )
    # 解析 FileCheck 命令：替换 %s 与 FileCheck 二进制路径。
    filecheck_args_list = list(
        filter(
            lambda x: x != "",
            commands[1]
            .replace("< ", " ")
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
            res_alive2, log_alive2 = alive2_check(
                new_input, output.decode(), additional_args
            )
            if source_program is not None and expect_optimized_program is not None:
                current_optimized_program = (
                    output.decode()
                    .removeprefix(
                        "; ModuleID = '<stdin>'\nsource_filename = \"<stdin>\"\n"
                    )
                    .removeprefix("\n")
                )
                res_cost, log_cost = cost_check(
                    source_program, expect_optimized_program, current_optimized_program
                )
            else:
                res_cost = False
                log_cost = ""
            res_filecheck, log_filecheck = filecheck_check(
                new_input, output.decode(), filecheck_args_list
            )
            log = {"alive2": log_alive2, "cost": log_cost, "filecheck": log_filecheck}
            if _alive2_log_is_infra(log_alive2):
                log["infra_error"] = True
                log["infra_reason"] = log_alive2.get("infra_reason")
            if source_program is not None and expect_optimized_program is not None:
                log["source_program"] = source_program
                log["expect_optimized_program"] = expect_optimized_program
                log["current_optimized_program"] = current_optimized_program
            if repro:
                # 复现阶段：应当触发失败，因此取反。
                res = not res_filecheck
            else:
                res = res_filecheck
                if not res:
                    # if filecheck fails, we use alive2_check and cost_check to check if the optimized program is correct and cost is low
                    if _alive2_log_is_infra(log_alive2):
                        log["opt_stderr"] = decode_output(out.stderr)
                        return (False, log)
                    res = res_alive2 and res_cost
            log["opt_stderr"] = decode_output(out.stderr)
            return (res, log)
        return (not repro, "success\n" + decode_output(out.stderr))
    except Exception as e:
        output = decode_output(getattr(e, "output", None))
        stderr = decode_output(getattr(e, "stderr", None))
        return (
            False,
            str(e) + "\n" + output + "\n" + stderr,
        )


def verify_test_group(repro: bool, input, type: str):
    """批量执行测试组（带 commands + test_body 的标准样本格式）。"""
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
                if _test_log_has_infra_error(log):
                    return (False, test_res)
                if repro:
                    overall_test_res = overall_test_res or res
                else:
                    overall_test_res = overall_test_res and res
    return (overall_test_res, test_res)


# Component name -> opt pass(es) for dataset-orig (no FileCheck, test_body empty)
_COMPONENT_TO_OPT_PASS = {
    "InstCombine": "instcombine",
    "InstructionSimplify": "instsimplify",
    "SimplifyCFG": "simplifycfg",
    "ConstraintElimination": "constraint-elimination",
    "CorrelatedValuePropagation": "correlated-propagation",
    "ValueTracking": "instcombine",
    "MemCpyOptimizer": "memcpyopt",
    "VectorCombine": "vector-combine",
    "PromoteMemoryToRegister": "mem2reg",
    "FunctionAttrs": "function-attrs",
    "Attributor": "attributor",
    "SimplifyLibCalls": "instcombine",
    "LazyValueInfo": "correlated-propagation",
    "ScalarEvolution": "indvars",
    "AliasAnalysis": "gvn",
    "Loads": "early-cse",
    "IR": "default<O2>",
    "Instrumentation": "default<O2>",
    "Scalar": "default<O2>",
}


def verify_test_group_orig(repro: bool, input_tests, type_str: str, component_list):
    """验证 dataset-orig 风格测试（commands 为空，靠 source/expect IR 比较）。

    行为：
    1. 根据组件推断 opt pass；
    2. 对 source_program 跑 opt 得到 current_optimized_program；
    3. 用 alive2 + cost 判断优化正确性与收益（无 FileCheck）。
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
                    if _test_log_has_infra_error(log):
                        return (False, test_res)
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
                        "log": {
                            "error": "missing source_program or expect_optimized_program"
                        },
                    }
                )
                overall_test_res = False
                continue
            try:
                print(f"opt_base: {opt_base}")
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
                if _alive2_log_is_infra(log_alive2):
                    log["infra_error"] = True
                    log["infra_reason"] = log_alive2.get("infra_reason")
                    res = False
                    test_res.append(
                        {
                            "file": file,
                            "args": "(no FileCheck)",
                            "name": name,
                            "body": "",
                            "result": False,
                            "log": log,
                        }
                    )
                    return (False, test_res)
                if repro:
                    res = res_alive2 and not res_cost
                else:
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
    """切换到 test_commit 的测试目录并执行 llvm-lit。"""
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
    """从测试结果列表中返回第一条失败样本（若无失败则返回 None）。"""
    for res in test_result:
        if not res["result"]:
            return res
    return None


def is_valid_fix(commit):
    """判断提交是否可作为“有效修复提交”。

    当前规则：
    1. 提交必须在 main 分支可达；
    2. 同时修改了 llvm/test 与 llvm/lib 或 llvm/include。
    """
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
