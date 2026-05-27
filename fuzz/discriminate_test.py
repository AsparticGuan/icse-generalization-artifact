#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 alive-mutate 生成「能区分 agent patch 与 dataset patch」的 LLVM IR 测试用例并严格校验。

判定标准（与 scripts/llvm_helper.py 一致）：
- 在 **agent patch** 构建的 opt 上：Alive2(source → opt 输出) 通过，且 cost_check 通过
  （current 代价同时不劣于 source 与 expect）。
- 在 **dataset（官方）patch** 构建的 opt 上：cost_check **不通过**（官方版本在你给出的 expect 意义下
  达不到同等优化），且 Alive2(source → opt 输出) 仍通过（说明 opt 输出是合法精化，避免 IR 损坏）。

IR 来源：仅从 dataset 的 **`tests` 字段**（各组内嵌套的 `tests` 条目）取首条 `source_program` 作为种子，调用 alive-mutate
（参数：`--disableAlive --saveAll`）在临时目录生成若干 `.ll`；对每条突变体，将 **expect** 设为在
agent 构建的 opt 上对该 IR 跑同一 `--passes` 管线的输出（作为 agent 可达的代价上界），再按上述标准筛选。

用法:
  cp agent/.env.example agent/.env   # 填写 LAB_LLVM_* 等（与 agent/run.py 相同）
  构建 alive-mutate：见 am/README.md（默认可执行文件 <repo>/am/build/alive-mutate）
  python fuzz/discriminate_test.py --patch-root results/agent/qwen3.5-plus

输出写入 results/discriminating_tests/<patch-root 末级目录名>/<issue>/<patch 时间戳>/accepted_tests.json，
与 results/agent/.../<issue>/<ts>/fix.json 一一对应，不同时间戳互不覆盖。

环境:
  见 agent/env_config.py
  LAB_DISCRIM_FUZZ_COUNT（默认 1000）、LAB_DISCRIM_FUZZ_TIMEOUT（单次 fuzz 超时基数，秒，默认 5）
  LAB_DISCRIM_ALIVE2_TIMEOUT（单次 Alive2/alive-tv 校验超时，秒，默认 120）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ALIVE2_TIMEOUT = float(
    os.environ.get("LAB_DISCRIM_ALIVE2_TIMEOUT", "120")
)
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
_PROJECT_DATASET_DIR = os.path.join(_PROJECT_ROOT, "dataset")

# 与 run.py 一致：强制仅使用仓库内 dataset/；须在导入 env_config / llvm_helper 之前设置。
os.environ["LAB_DATASET_DIR"] = _PROJECT_DATASET_DIR

sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))
sys.path.insert(0, _THIS_DIR)
_AGENT_DIR = os.path.join(_PROJECT_ROOT, "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from env_config import cfg  # noqa: E402

cfg.dataset_dir = _PROJECT_DATASET_DIR


def _ensure_writable_tmp_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    if not os.access(path, os.W_OK | os.X_OK):
        raise PermissionError(f"LAB_TMP_DIR is not writable: {path}")
    return path


os.environ.setdefault("LAB_LLVM_DIR", cfg.llvm_dir)
os.environ.setdefault("LAB_LLVM_BUILD_DIR", cfg.llvm_build_dir)
os.environ["LAB_DATASET_DIR"] = cfg.dataset_dir
os.environ.setdefault("LAB_LLVM_ALIVE_TV", cfg.alive_tv)
os.environ.setdefault("LAB_LLVM_COST_TOOL", cfg.cost_tool)
_tmp_dir = os.environ.get("LAB_TMP_DIR", cfg.tmp_dir)
if not _tmp_dir:
    _tmp_dir = cfg.tmp_dir
_tmp_dir = _ensure_writable_tmp_dir(_tmp_dir)
os.environ["LAB_TMP_DIR"] = _tmp_dir
os.environ["TMPDIR"] = _tmp_dir

import llvm_helper  # noqa: E402
from lab_env import Environment as LabEnvironment  # noqa: E402
import retest_patches as rp  # noqa: E402

DATASET_DIR = cfg.dataset_dir


def extract_passes_for_fuzzer(run_line: str) -> str:
    m = re.search(r"-passes='([^']+)'", run_line)
    if m:
        return m.group(1).strip()
    m = re.search(r'-passes="([^"]+)"', run_line)
    if m:
        return m.group(1).strip()
    m = re.search(r"-passes=(\S+)", run_line)
    if m:
        return m.group(1).strip().strip("'\"")
    return "instcombine"


def find_first_opt_run_line(data: dict[str, Any]) -> str | None:
    for group in data.get("dev_tests") or []:
        cmds = group.get("commands") or []
        if not cmds or not cmds[0]:
            continue
        run = cmds[0][0]
        if isinstance(run, str) and "opt" in run:
            return run
    return None


def default_passes_from_hints(data: dict[str, Any]) -> str:
    comps = (data.get("hints") or {}).get("components") or []
    if not comps:
        return "instcombine"
    c0 = str(comps[0])
    if "SimplifyCFG" in c0:
        return "simplifycfg"
    if "InstCombine" in c0:
        return "instcombine"
    if "Constraint" in c0:
        return "constraint-elimination"
    return "instcombine"


def fuzz_collect_ll_files(
    *,
    mutator_bin: Path,
    ll_dir: Path,
    passes: str,
    target_ll: int,
    per_fuzz_timeout: float,
    seed_ll: str | None,
) -> list[Path]:
    ll_dir.mkdir(parents=True, exist_ok=True)
    for p in ll_dir.glob("*.ll"):
        p.unlink(missing_ok=True)

    if not seed_ll or target_ll <= 0:
        return []

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ll", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(seed_ll)
        seed_path = tf.name

    try:
        timeout_s = max(60.0, per_fuzz_timeout * max(1, target_ll))
        subprocess.run(
            [
                str(mutator_bin),
                seed_path,
                str(ll_dir),
                "-n",
                str(target_ll),
                "--passes",
                passes,
                "--disableAlive",
                "--saveAll",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.unlink(seed_path)
        except OSError:
            pass

    return sorted(ll_dir.glob("*.ll"))


def _alive2_check_with_timeout(
    src: str,
    tgt: str,
    additional_args: str | None,
    timeout_sec: float,
) -> tuple[bool, dict[str, Any]]:
    """仅为 llvm_helper.alive2_check 内部的 alive-tv `check_output` 注入 timeout（不改 scripts/llvm_helper.py）。"""
    real_co = subprocess.check_output

    def _check_output_timed(*args: Any, **kwargs: Any) -> bytes:
        kwargs.setdefault("timeout", timeout_sec)
        return real_co(*args, **kwargs)

    subprocess.check_output = _check_output_timed  # type: ignore[method-assign]
    try:
        return llvm_helper.alive2_check(src, tgt, additional_args)
    finally:
        subprocess.check_output = real_co  # type: ignore[method-assign]


def _safe_path_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value)


def _resolve_patch_files_with_timestamp(
    patch_root: str, issue_ids: list[str] | None, patch_ts: str
) -> list[tuple[str, str, str]]:
    """Resolve to [(issue_id, patch_fix_json_path, run_timestamp)] for a fixed timestamp."""
    if not os.path.isdir(patch_root):
        print(f"[ERROR] Patch dir not found: {patch_root}")
        return []

    target_ts = patch_ts.strip()
    if not target_ts:
        return []

    def _fix_at(issue_name: str, issue_dir: str) -> tuple[str, str, str] | None:
        fix_path = os.path.join(issue_dir, target_ts, "fix.json")
        if os.path.isfile(fix_path):
            return issue_name, fix_path, target_ts
        return None

    items: list[tuple[str, str, str]] = []

    if issue_ids is None:
        direct_issue_name = os.path.basename(os.path.abspath(patch_root))
        direct = _fix_at(direct_issue_name, patch_root)
        if direct is not None:
            return [direct]

        for issue_name in sorted(os.listdir(patch_root)):
            issue_dir = os.path.join(patch_root, issue_name)
            if not os.path.isdir(issue_dir):
                continue
            if issue_name.startswith("retest-"):
                continue
            matched = _fix_at(issue_name, issue_dir)
            if matched is not None:
                items.append(matched)
        return items

    for raw in issue_ids:
        issue_id = raw.strip()
        if issue_id.endswith(".json"):
            issue_id = issue_id[:-5]
        if not issue_id:
            continue

        candidates = [
            (issue_id, os.path.join(patch_root, issue_id)),
            (_safe_path_part(issue_id), os.path.join(patch_root, _safe_path_part(issue_id))),
        ]
        found = False
        for issue_name, issue_dir in candidates:
            if not os.path.isdir(issue_dir):
                continue
            matched = _fix_at(issue_name, issue_dir)
            if matched is not None:
                items.append(matched)
                found = True
                break
            found = True
            print(
                f"[WARN] Timestamp dir not found for {raw}: "
                f"{os.path.join(issue_dir, target_ts, 'fix.json')}"
            )
            break

        if found:
            continue

        safe_issue = _safe_path_part(issue_id)
        suffixes = (f"-{issue_id}", f"-{safe_issue}")
        matched_issue_dirs: list[tuple[str, str]] = []
        for issue_name in sorted(os.listdir(patch_root)):
            issue_dir = os.path.join(patch_root, issue_name)
            if not os.path.isdir(issue_dir):
                continue
            if issue_name.endswith(suffixes):
                matched_issue_dirs.append((issue_name, issue_dir))

        if len(matched_issue_dirs) == 1:
            issue_name, issue_dir = matched_issue_dirs[0]
            matched = _fix_at(issue_name, issue_dir)
            if matched is not None:
                items.append(matched)
            else:
                print(
                    f"[WARN] Timestamp dir not found for {raw}: "
                    f"{os.path.join(issue_dir, target_ts, 'fix.json')}"
                )
        elif len(matched_issue_dirs) > 1:
            print(
                f"[WARN] Ambiguous historical issue dirs for {raw}: "
                + ", ".join(name for name, _ in matched_issue_dirs)
                + ". Please set --patch-root to one model root."
            )
        else:
            print(f"[WARN] Issue dir not found for {raw} in {patch_root}")

    return items


def _strip_diff_only(patch: str) -> str:
    idx = patch.find("diff --git")
    if idx >= 0:
        return patch[idx:]
    return patch


def _passes_pipeline_from_dataset(dataset: dict[str, Any]) -> str:
    run_line = find_first_opt_run_line(dataset)
    if run_line:
        return extract_passes_for_fuzzer(run_line)
    return default_passes_from_hints(dataset)


def _build_intrinsic_decl_table() -> dict[str, str]:
    decls: dict[str, str] = {
        "llvm.fabs.f16": "declare half @llvm.fabs.f16(half)",
        "llvm.fabs.f32": "declare float @llvm.fabs.f32(float)",
        "llvm.fabs.f64": "declare double @llvm.fabs.f64(double)",
        "llvm.fabs.f80": "declare x86_fp80 @llvm.fabs.f80(x86_fp80)",
        "llvm.fabs.f128": "declare fp128 @llvm.fabs.f128(fp128)",
    }
    for bits in (8, 16, 32, 64):
        ty = f"i{bits}"
        decls[f"llvm.smax.{ty}"] = f"declare {ty} @llvm.smax.{ty}({ty}, {ty})"
        decls[f"llvm.smin.{ty}"] = f"declare {ty} @llvm.smin.{ty}({ty}, {ty})"
        decls[f"llvm.umax.{ty}"] = f"declare {ty} @llvm.umax.{ty}({ty}, {ty})"
        decls[f"llvm.umin.{ty}"] = f"declare {ty} @llvm.umin.{ty}({ty}, {ty})"
    return decls


_INTRINSIC_DECLS = _build_intrinsic_decl_table()


def _seed_has_intrinsic_decl(seed_ir: str, intrinsic_name: str) -> bool:
    pat = rf"^\s*(?:declare|define)\b[^\n@]*@{re.escape(intrinsic_name)}\s*\("
    return re.search(pat, seed_ir, flags=re.MULTILINE) is not None


def _inject_missing_intrinsic_decls(seed_ir: str) -> tuple[str, list[str]]:
    intrinsic_names = sorted(
        {
            m.group(1)
            for m in re.finditer(r"@((?:llvm)\.[A-Za-z0-9_$.]+)", seed_ir)
            if m.group(1).startswith("llvm.")
        }
    )
    missing_known: list[str] = []
    decl_lines: list[str] = []
    for name in intrinsic_names:
        if _seed_has_intrinsic_decl(seed_ir, name):
            continue
        decl = _INTRINSIC_DECLS.get(name)
        if not decl:
            continue
        missing_known.append(name)
        decl_lines.append(decl)

    if not decl_lines:
        return seed_ir, []
    patched = seed_ir.rstrip() + "\n\n" + "\n".join(decl_lines) + "\n"
    return patched, missing_known


def _dataset_seed_source(dataset: dict[str, Any]) -> str | None:
    for tg in dataset.get("tests") or []:
        for t in tg.get("tests") or []:
            sp = t.get("source_program")
            if isinstance(sp, str) and sp.strip():
                return sp.strip()
    return None


def _strip_opt_module_prefix(text: str) -> str:
    return text.removeprefix(
        "; ModuleID = '<stdin>'\nsource_filename = \"<stdin>\"\n"
    ).removeprefix("\n")


def _run_opt(source_ll: str, pass_name: str) -> tuple[bool, str, str]:
    opt_bin = os.path.join(llvm_helper.llvm_build_dir, "bin/opt")
    if not os.path.isfile(opt_bin):
        return False, "", f"opt not found: {opt_bin}"
    cmd = [opt_bin, f"--passes={pass_name}", "-S"]
    try:
        proc = subprocess.run(
            cmd,
            input=source_ll.encode(),
            timeout=30.0,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode(errors="replace")
            return False, "", err or f"opt exit {proc.returncode}"
        out = proc.stdout.decode(errors="replace")
        return True, out, ""
    except Exception as e:
        return False, "", str(e)


def _eval_opt_side(
    source_program: str,
    expect_optimized_program: str,
    pass_name: str,
    *,
    alive2_timeout: float,
) -> dict[str, Any]:
    """在当前 llvm_helper.llvm_build_dir 上跑 opt + alive2 + cost，返回结构化日志。"""
    ok, output, err = _run_opt(source_program, pass_name)
    if not ok:
        return {
            "opt_ok": False,
            "opt_error": err,
            "alive2_ok": False,
            "cost_ok": False,
        }

    new_input = llvm_helper.copy_triple(source_program, output.encode())
    new_input = llvm_helper.copy_datalayout(new_input, output.encode())
    res_alive, log_alive = _alive2_check_with_timeout(
        new_input, output, None, alive2_timeout
    )
    cur = _strip_opt_module_prefix(output)
    res_cost, costs = llvm_helper.cost_check(
        source_program, expect_optimized_program, cur
    )
    return {
        "opt_ok": True,
        "alive2_ok": bool(res_alive),
        "cost_ok": bool(res_cost),
        "alive2": log_alive,
        "costs": costs,
        "current_optimized_program": cur,
    }


def _validate_candidate(
    source_program: str,
    expect_optimized_program: str,
    pass_name: str,
    *,
    alive2_timeout: float,
) -> tuple[bool, dict[str, Any]]:
    """在当前 llvm_build_dir 上校验候选：opt、Alive2、cost 均通过则 ok 为 True。"""
    log = _eval_opt_side(
        source_program,
        expect_optimized_program,
        pass_name,
        alive2_timeout=alive2_timeout,
    )
    ok = bool(
        log.get("opt_ok") and log.get("alive2_ok") and log.get("cost_ok")
    )
    return ok, log


def prepare_build(
    *,
    llvm_dir: str,
    build_dir: str,
    base_commit: str,
    patch_text: str,
    max_jobs: int,
    additional_cmake: list[str],
) -> tuple[bool, str]:
    llvm_helper.llvm_dir = llvm_dir
    llvm_helper.llvm_build_dir = build_dir
    os.environ["LAB_LLVM_DIR"] = llvm_dir
    llvm_helper.reset(base_commit)
    ok, log = llvm_helper.apply(_strip_diff_only(patch_text))
    if not ok:
        return False, f"apply failed: {log}"
    os.makedirs(build_dir, exist_ok=True)
    res, blog = llvm_helper.build(max_jobs, additional_cmake)
    if not res:
        return False, f"build failed: {blog[-4000:]}"
    return True, ""


def process_issue(
    *,
    patch_issue_id: str,
    fix_path: str,
    patch_ts: str,
    patch_root: str,
    mutator_bin: str,
    fuzz_count: int,
    fuzz_timeout: float,
    alive2_timeout: float,
    require_fast_pass: bool,
    override: bool,
) -> None:
    test_issue_id = rp.resolve_test_issue_id(patch_issue_id, DATASET_DIR)
    if not test_issue_id:
        print(f"[SKIP] 无法解析 dataset issue: {patch_issue_id}")
        return

    ds_path = os.path.join(DATASET_DIR, f"{test_issue_id}.json")
    with open(ds_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    if not dataset.get("verified", False):
        print(f"[SKIP] {test_issue_id} dataset 未 verified")
        return

    with open(fix_path, "r", encoding="utf-8") as f:
        fix_data = json.load(f)

    if require_fast_pass and fix_data.get("fast_check_pass") is not True:
        print(f"[SKIP] {patch_issue_id} fast_check_pass 非 true")
        return

    patches = rp._collect_patches_to_test(fix_data)
    if not patches:
        print(f"[SKIP] {patch_issue_id} 无可用 patch")
        return

    agent_patch = patches[0]["patch"]
    dataset_patch = dataset.get("patch") or ""
    if not dataset_patch.strip():
        print(f"[SKIP] {test_issue_id} dataset 缺少 patch 字段")
        return

    passes_pipeline = _passes_pipeline_from_dataset(dataset)
    seed_ll = _dataset_seed_source(dataset)
    if not seed_ll:
        print(
            f"[SKIP] {test_issue_id} dataset 的 tests 字段中无可用 source_program 作为 alive-mutate 种子"
        )
        return
    seed_ll, injected_intrinsics = _inject_missing_intrinsic_decls(seed_ll)
    if injected_intrinsics:
        print(
            f"[SEED] {test_issue_id} 自动补充 intrinsic 声明: "
            + ", ".join(injected_intrinsics)
        )

    mutator_path = Path(mutator_bin).expanduser()
    if not mutator_path.is_file():
        print(f"[FAIL] 找不到 alive-mutate: {mutator_path}（可用 --mutator-bin 指定）")
        return

    llvm_dir = rp.ensure_llvm_clone_for_issue(test_issue_id)
    base_root = os.environ["LAB_LLVM_BUILD_DIR"]
    build_agent = os.path.join(base_root, f"{test_issue_id}_disc_agent")
    build_dataset = os.path.join(base_root, f"{test_issue_id}_disc_dataset")

    try:
        env = LabEnvironment(test_issue_id, os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z"))
    except Exception as e:
        print(f"[SKIP] LabEnvironment 初始化失败 {test_issue_id}: {e}")
        return

    print(f"[BUILD] {test_issue_id} agent side -> {build_agent}")
    ok, err = prepare_build(
        llvm_dir=llvm_dir,
        build_dir=build_agent,
        base_commit=env.base_commit,
        patch_text=agent_patch,
        max_jobs=env.max_build_jobs,
        additional_cmake=env.additional_cmake_args,
    )
    if not ok:
        print(f"[FAIL] agent 构建: {err}")
        return

    print(f"[BUILD] {test_issue_id} dataset side -> {build_dataset}")
    ok, err = prepare_build(
        llvm_dir=llvm_dir,
        build_dir=build_dataset,
        base_commit=env.base_commit,
        patch_text=dataset_patch,
        max_jobs=env.max_build_jobs,
        additional_cmake=env.additional_cmake_args,
    )
    if not ok:
        print(f"[FAIL] dataset 构建: {err}")
        return

    safe_model_root = _safe_path_part(os.path.basename(os.path.normpath(patch_root)))
    out_dir = os.path.join(
        _PROJECT_ROOT,
        "results",
        "discriminating_tests",
        safe_model_root,
        _safe_path_part(patch_issue_id),
    )
    ts_part = (patch_ts or "").strip()
    if ts_part:
        out_dir = os.path.join(out_dir, _safe_path_part(ts_part))
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, "accepted_tests.json")
    if os.path.isfile(manifest_path) and not override:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {
            "patch_issue_id": patch_issue_id,
            "test_issue_id": test_issue_id,
            "patch_source": fix_path,
            "patch_timestamp": patch_ts,
            "build_agent_dir": build_agent,
            "build_dataset_dir": build_dataset,
            "tests": [],
        }
    manifest["passes_pipeline"] = passes_pipeline
    manifest["pass_name"] = passes_pipeline
    manifest["mutator_bin"] = str(mutator_path.resolve())
    manifest["fuzz_count_target"] = fuzz_count
    manifest["alive2_timeout_sec"] = alive2_timeout

    accepted: list[dict[str, Any]] = manifest.setdefault("tests", [])
    seen = {hash(t.get("source_program", "")) for t in accepted}

    print(
        f"[FUZZ] alive-mutate passes={passes_pipeline!r} "
        f"目标 {fuzz_count} 个突变体（mutator={mutator_path}），"
        f"Alive2 超时 {alive2_timeout}s"
    )
    round_accepted = 0
    with tempfile.TemporaryDirectory() as td:
        ll_dir = Path(td) / "mutants"
        ll_files = fuzz_collect_ll_files(
            mutator_bin=mutator_path,
            ll_dir=ll_dir,
            passes=passes_pipeline,
            target_ll=fuzz_count,
            per_fuzz_timeout=fuzz_timeout,
            seed_ll=seed_ll,
        )
        manifest["fuzz_generated"] = len(ll_files)
        for idx, llp in enumerate(ll_files, 1):
            src = llp.read_text(encoding="utf-8", errors="replace").strip()
            if not src:
                continue
            h = hash(src)
            if h in seen:
                continue

            llvm_helper.llvm_build_dir = build_agent
            ok_e, exp, _err_e = _run_opt(src, passes_pipeline)
            if not ok_e or not exp.strip():
                continue

            ok_agent, log_agent = _validate_candidate(
                src, exp, passes_pipeline, alive2_timeout=alive2_timeout
            )
            if not ok_agent:
                continue

            llvm_helper.llvm_build_dir = build_dataset
            _ok_ds_dummy, log_ds = _validate_candidate(
                src, exp, passes_pipeline, alive2_timeout=alive2_timeout
            )
            ds_alive = bool(log_ds.get("alive2_ok"))
            ds_cost_ok = bool(log_ds.get("cost_ok"))
            if ds_cost_ok or not ds_alive:
                continue

            name = f"mut_{llp.stem}"
            entry = {
                "test_name": name,
                "source_program": src,
                "expect_optimized_program": exp,
                "generator": "alive-mutate",
                "mutant_file": llp.name,
                "validation": {
                    "agent": log_agent,
                    "dataset": log_ds,
                },
            }
            accepted.append(entry)
            seen.add(h)
            round_accepted += 1
            if idx % 50 == 0:
                manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2, ensure_ascii=False)

    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"[FUZZ] 扫描 {len(ll_files)} 个突变体，新接受 +{round_accepted}，累计 {len(accepted)}")
    print(f"[DONE] {test_issue_id} 写入 {manifest_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成并校验区分 agent/dataset patch 的测试用例")
    ap.add_argument(
        "--patch-root",
        default=os.path.join(_PROJECT_ROOT, "results", "agent", "qwen3.5-plus"),
        help="agent 结果根目录（含 <issue>/<ts>/fix.json）",
    )
    ap.add_argument(
        "--issues",
        default="",
        help="逗号分隔 issue id；默认处理 patch-root 下全部含 fix.json 的 issue",
    )
    ap.add_argument(
        "--patch-ts",
        default="",
        help="仅处理指定时间戳目录（如 20260330-004829）；会扫描该时间戳对应的 run",
    )
    ap.add_argument(
        "--mutator-bin",
        default="",
        help="alive-mutate 可执行文件；默认 <repo>/am/build/alive-mutate",
    )
    ap.add_argument(
        "--fuzz-count",
        type=int,
        default=int(os.environ.get("LAB_DISCRIM_FUZZ_COUNT", "1000")),
        help="alive-mutate 目标生成突变体数量（默认 1000）",
    )
    ap.add_argument(
        "--fuzz-timeout",
        type=float,
        default=float(os.environ.get("LAB_DISCRIM_FUZZ_TIMEOUT", "5")),
        help="传给 alive-mutate 单次调用的超时基数（秒），总超时为 max(60, 此值×目标数量)",
    )
    ap.add_argument(
        "--alive2-timeout",
        type=float,
        default=_DEFAULT_ALIVE2_TIMEOUT,
        help=(
            "单次 Alive2(alive-tv) 校验超时（秒）；"
            "默认读环境变量 LAB_DISCRIM_ALIVE2_TIMEOUT，未设置则为 120"
        ),
    )
    ap.add_argument(
        "--require-fast-pass",
        action="store_true",
        help="仅处理 fix.json 中 fast_check_pass==true 的 run",
    )
    ap.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="覆盖已有的 accepted_tests.json",
    )
    args = ap.parse_args()

    mut_bin = args.mutator_bin.strip()
    if not mut_bin:
        mut_bin = os.path.join(_PROJECT_ROOT, "am", "build", "alive-mutate")

    issue_list = [x.strip() for x in args.issues.split(",") if x.strip()]
    issue_filter = issue_list if issue_list else None

    if args.patch_ts.strip():
        items = _resolve_patch_files_with_timestamp(
            args.patch_root, issue_filter, args.patch_ts
        )
    elif issue_filter:
        items = []
        for iid in issue_filter:
            resolved = rp.resolve_patch_files(args.patch_root, [iid])
            items.extend(resolved)
    else:
        items = rp.resolve_patch_files(args.patch_root, None)

    if not items:
        if args.patch_ts.strip():
            print(f"[WARN] 没有可处理的 fix.json（未匹配到 patch_ts={args.patch_ts.strip()}）")
        else:
            print("[WARN] 没有可处理的 fix.json")
        return

    for patch_issue_id, fix_path, patch_ts in items:
        process_issue(
            patch_issue_id=patch_issue_id,
            fix_path=fix_path,
            patch_ts=patch_ts,
            patch_root=args.patch_root,
            mutator_bin=mut_bin,
            fuzz_count=max(0, args.fuzz_count),
            fuzz_timeout=max(0.1, args.fuzz_timeout),
            alive2_timeout=max(0.1, args.alive2_timeout),
            require_fast_pass=args.require_fast_pass,
            override=args.force,
        )


if __name__ == "__main__":
    main()


# python fuzz/discriminate_test.py --patch-root /app/data/auto-opt/results/agent/deepseek-reasoner --patch-ts 20260416-091504