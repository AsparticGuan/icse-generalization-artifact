#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Retest agent patches on dataset dev_tests.

Default behavior:
  - Read patches from results/agent/<safe_model>/<safe_issue>/<timestamp>/fix.json
    (for each issue, select the latest timestamp directory containing fix.json)
  - Also supports historical layout: results/agent/<safe_model>-<safe_issue>/<timestamp>/fix.json
  - Only process patches with fast_check_pass == true
  - If full_check_pass == false, use fast_full_mismatch_patch
  - If full_check_pass == true, use patch
  - Validate with dataset/<issue_id>.json "dev_tests"

Usage:
  python agent/retest_patches.py
  python agent/retest_patches.py 85250,76128 -f

Env overrides:
  LAB_PATCH_DIR   - model-level patch source directory
                    (default: results/agent/<safe_model>)
  LAB_RETEST_DIR  - retest output root directory
                    (default: results/agent/<safe_model>)

Retest output file:
  <retest_root>/<safe_issue>/<timestamp>/retest.json
By default, this becomes:
  results/agent/<safe_model>/<safe_issue>/<timestamp>/retest.json
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))


def _safe_path_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value)


_DEFAULT_MODEL = _safe_path_part(os.environ.get("LAB_LLM_MODEL", "gpt-4o"))
_DEFAULT_PATCH_DIR = os.path.join(_PROJECT_ROOT, "results", "agent", _DEFAULT_MODEL)

DEFAULT_PATCH_DIR = os.environ.get("LAB_PATCH_DIR", _DEFAULT_PATCH_DIR)
DEFAULT_RETEST_ROOT = os.environ.get("LAB_RETEST_DIR", _DEFAULT_PATCH_DIR)

DATASET_DIR = os.environ.get("LAB_DATASET_DIR")
if not DATASET_DIR:
    DATASET_DIR = os.path.join(_PROJECT_ROOT, "dataset")
    os.environ["LAB_DATASET_DIR"] = DATASET_DIR

# Ensure required envs exist before importing llvm_helper.
REQUIRED_ENVS = [
    "LAB_LLVM_DIR",
    "LAB_LLVM_BUILD_DIR",
    "LAB_LLVM_ALIVE_TV",
    "LAB_LLVM_COST_TOOL",
    "LAB_DATASET_DIR",
]
missing_envs = [k for k in REQUIRED_ENVS if not os.environ.get(k)]
if missing_envs:
    print(
        "[ERROR] Missing required env(s): "
        + ", ".join(missing_envs)
        + ". Please source init_env.sh first."
    )
    sys.exit(1)

sys.path.append(os.path.join(os.path.dirname(os.environ["LAB_DATASET_DIR"]), "scripts"))
sys.path.append(os.path.join(_PROJECT_ROOT, "scripts"))

import llvm_helper  # pyright: ignore[reportMissingImports]  # noqa: E402
from lab_env import (
    Environment as Env,
)  # pyright: ignore[reportMissingImports]  # noqa: E402

BASEMODEL_CUTOFF = os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")
MAX_BUILD_JOBS = int(os.environ.get("LAB_MAX_BUILD_JOBS", os.cpu_count()))
BASE_LLVM_DIR = os.environ.get("LAB_LLVM_DIR")
UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")


def ensure_llvm_clone_for_issue(issue_id: str) -> str:
    issue_dir = os.path.join(UTILS_DIR, f"llvm-{issue_id}")
    if os.path.isdir(issue_dir):
        return issue_dir
    os.makedirs(UTILS_DIR, exist_ok=True)
    if BASE_LLVM_DIR and os.path.isdir(BASE_LLVM_DIR):
        print(f"[INFO] Cloning llvm-project from {BASE_LLVM_DIR} to {issue_dir}")
        subprocess.check_call(["git", "clone", BASE_LLVM_DIR, issue_dir])
    else:
        print(f"[INFO] Cloning llvm-project from remote to {issue_dir}")
        subprocess.check_call(
            ["git", "clone", "https://github.com/llvm/llvm-project.git", issue_dir]
        )
    return issue_dir


def parse_args(argv: list[str]) -> tuple[list[str] | None, bool]:
    override = False
    issue_ids: list[str] | None = None

    args = argv[1:]
    if not args:
        return issue_ids, override

    if args[0] == "-f":
        return issue_ids, True

    raw = args[0].strip()
    if raw:
        issue_ids = [x.strip() for x in raw.split(",") if x.strip()]
    if len(args) >= 2 and args[1] == "-f":
        override = True
    return issue_ids, override


def _find_latest_fix(issue_dir: str) -> tuple[str, str] | None:
    """Return (timestamp, fix_json_path) for the latest run under one issue dir."""
    candidates: list[tuple[str, str]] = []
    for ts in os.listdir(issue_dir):
        run_dir = os.path.join(issue_dir, ts)
        if not os.path.isdir(run_dir):
            continue
        fix_path = os.path.join(run_dir, "fix.json")
        if os.path.isfile(fix_path):
            candidates.append((ts, fix_path))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0]


def resolve_patch_files(
    patch_dir: str, issue_ids: list[str] | None
) -> list[tuple[str, str, str]]:
    """Resolve to [(issue_id, patch_fix_json_path, run_timestamp)]."""
    patch_items: list[tuple[str, str, str]] = []
    if not os.path.isdir(patch_dir):
        print(f"[ERROR] Patch dir not found: {patch_dir}")
        return []

    if issue_ids is None:
        # 兼容 patch_dir 直接指向某个 issue 目录（其下为 <timestamp>/fix.json）。
        latest_self = _find_latest_fix(patch_dir)
        if latest_self is not None:
            ts, fix_path = latest_self
            issue_name = os.path.basename(os.path.abspath(patch_dir))
            return [(issue_name, fix_path, ts)]

        for issue_name in sorted(os.listdir(patch_dir)):
            issue_dir = os.path.join(patch_dir, issue_name)
            if not os.path.isdir(issue_dir):
                continue
            # Avoid treating retest output directories as issue directories.
            if issue_name.startswith("retest-"):
                continue
            latest = _find_latest_fix(issue_dir)
            if latest is None:
                continue
            ts, fix_path = latest
            patch_items.append((issue_name, fix_path, ts))
        return patch_items

    for raw in issue_ids:
        issue_id = raw.strip()
        if issue_id.endswith(".json"):
            issue_id = issue_id[:-5]

        candidate_issue_dirs = [
            (issue_id, os.path.join(patch_dir, issue_id)),
            (
                _safe_path_part(issue_id),
                os.path.join(patch_dir, _safe_path_part(issue_id)),
            ),
        ]

        found_issue_dir = None
        found_issue_name = issue_id
        for issue_name, issue_dir in candidate_issue_dirs:
            if os.path.isdir(issue_dir):
                found_issue_dir = issue_dir
                found_issue_name = issue_name
                break

        if not found_issue_dir:
            # 兼容历史目录：<safe_model>-<safe_issue>
            safe_issue = _safe_path_part(issue_id)
            suffixes = (f"-{issue_id}", f"-{safe_issue}")
            matched_issue_dirs: list[tuple[str, str]] = []
            for issue_name in sorted(os.listdir(patch_dir)):
                issue_dir = os.path.join(patch_dir, issue_name)
                if not os.path.isdir(issue_dir):
                    continue
                if issue_name.endswith(suffixes):
                    matched_issue_dirs.append((issue_name, issue_dir))

            if len(matched_issue_dirs) == 1:
                found_issue_name, found_issue_dir = matched_issue_dirs[0]
            elif len(matched_issue_dirs) > 1:
                print(
                    f"[WARN] Ambiguous historical issue dirs for {raw}: "
                    + ", ".join(name for name, _ in matched_issue_dirs)
                    + ". Please set LAB_PATCH_DIR to one model root."
                )
                continue

        if not found_issue_dir:
            print(f"[WARN] Issue dir not found for {raw} in {patch_dir}")
            continue

        latest = _find_latest_fix(found_issue_dir)
        if latest is None:
            print(f"[WARN] No fix.json found under issue dir: {found_issue_dir}")
            continue

        ts, fix_path = latest
        patch_items.append((found_issue_name, fix_path, ts))

    return patch_items


def _resolve_test_issue_id_direct(patch_issue_id: str, dataset_dir: str) -> str | None:
    """按原有规则直接解析 issue_id（不做前缀剥离）。"""
    if patch_issue_id.endswith("-orig"):
        base = patch_issue_id[: -len("-orig")]
        base_path = os.path.join(dataset_dir, f"{base}.json")
        if os.path.exists(base_path):
            return base
    cand_path = os.path.join(dataset_dir, f"{patch_issue_id}.json")
    if os.path.exists(cand_path):
        return patch_issue_id
    if not patch_issue_id.endswith("-orig"):
        orig = f"{patch_issue_id}-orig"
        orig_path = os.path.join(dataset_dir, f"{orig}.json")
        if os.path.exists(orig_path):
            return orig
    return None


def resolve_test_issue_id(patch_issue_id: str, dataset_dir: str) -> str | None:
    """解析测试用 issue_id，兼容历史目录名中的模型前缀。"""
    direct = _resolve_test_issue_id_direct(patch_issue_id, dataset_dir)
    if direct:
        return direct

    # 历史输出目录可能形如 <safe_model>-<safe_issue>。
    # 当直接匹配失败时，按 '-' 分段尝试右侧后缀。
    parts = [x for x in patch_issue_id.split("-") if x]
    for start in range(1, len(parts)):
        suffix = "-".join(parts[start:])
        resolved = _resolve_test_issue_id_direct(suffix, dataset_dir)
        if resolved:
            return resolved

    return None


def write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _collect_patches_to_test(patch_data: dict) -> list[dict]:
    """Extract testable patches from patch JSON (new func_results format or legacy)."""
    patches: list[dict] = []

    func_results = patch_data.get("func_results", [])
    if func_results:
        for fr in func_results:
            p = fr.get("patch", "")
            if isinstance(p, str) and p.strip():
                patches.append(
                    {
                        "patch": p,
                        "func_name": fr.get("func_name"),
                        "file_path": fr.get("file_path"),
                        "source_check_fast_pass": fr.get("check_fast_pass"),
                        "source_check_full_pass": fr.get("check_full_pass"),
                    }
                )

    if not patches:
        fast_check_pass = patch_data.get("fast_check_pass")
        if fast_check_pass is not True:
            return []
        full_check_pass = patch_data.get("full_check_pass")
        patch_key = "fast_full_mismatch_patch" if full_check_pass is False else "patch"
        patch = patch_data.get(patch_key)
        if isinstance(patch, str) and patch.strip():
            patches.append(
                {
                    "patch": patch,
                    "func_name": None,
                    "file_path": None,
                    "source_check_fast_pass": fast_check_pass,
                    "source_check_full_pass": full_check_pass,
                }
            )

    return patches


def retest_patch(
    patch_issue_id: str,
    patch_path: str,
    patch_timestamp: str,
    output_root: str,
    *,
    override: bool = False,
) -> None:
    issue_part = _safe_path_part(patch_issue_id)
    out_path = os.path.join(output_root, issue_part, patch_timestamp, "retest.json")
    if not override and os.path.exists(out_path):
        print(f"[SKIP] {patch_issue_id} exists at {out_path} (use -f to override)")
        return

    print(f"[INFO] Retesting patch {patch_issue_id} from {patch_path}")
    try:
        with open(patch_path, "r", encoding="utf-8") as f:
            patch_data = json.load(f)
    except Exception as e:
        write_json(out_path, {"error": f"Failed to read patch JSON: {e}"})
        return

    patches_to_test = _collect_patches_to_test(patch_data)
    if not patches_to_test:
        print(f"[SKIP] {patch_issue_id} no testable patches in {patch_path}")
        return

    test_issue_id = resolve_test_issue_id(patch_issue_id, DATASET_DIR)
    if not test_issue_id:
        write_json(
            out_path,
            {
                "error": "Dataset issue not found",
                "patch_issue_id": patch_issue_id,
            },
        )
        return

    dataset_path = os.path.join(DATASET_DIR, f"{test_issue_id}.json")
    try:
        with open(dataset_path, "r", encoding="utf-8") as f:
            dataset_data = json.load(f)
    except Exception as e:
        write_json(
            out_path,
            {
                "error": f"Failed to load dataset: {e}",
                "patch_issue_id": patch_issue_id,
                "test_issue_id": test_issue_id,
            },
        )
        return

    dev_tests = dataset_data.get("dev_tests") or []
    if not dev_tests:
        write_json(
            out_path,
            {
                "error": "No dev_tests in dataset",
                "patch_issue_id": patch_issue_id,
                "test_issue_id": test_issue_id,
            },
        )
        return

    issue_llvm_dir = ensure_llvm_clone_for_issue(test_issue_id)
    llvm_helper.llvm_dir = issue_llvm_dir
    os.environ["LAB_LLVM_DIR"] = issue_llvm_dir

    build_root = os.environ["LAB_LLVM_BUILD_DIR"]
    llvm_helper.llvm_build_dir = os.path.join(build_root, test_issue_id)
    build_cache_dir = os.path.join(build_root, test_issue_id + "_cache")

    try:
        env = Env(test_issue_id, BASEMODEL_CUTOFF, max_build_jobs=MAX_BUILD_JOBS)
    except Exception as e:
        write_json(out_path, {"error": f"Failed to init env: {e}"})
        return

    if not env.data.get("verified", False):
        write_json(
            out_path,
            {
                "error": "Issue not verified",
                "patch_issue_id": patch_issue_id,
                "test_issue_id": test_issue_id,
            },
        )
        return

    retest_results = []
    for i, pt in enumerate(patches_to_test):
        label = pt["func_name"] or f"patch_{i}"
        print(f"  [{i + 1}/{len(patches_to_test)}] Testing {label}")

        env.reset()
        if os.path.exists(build_cache_dir):
            shutil.rmtree(llvm_helper.llvm_build_dir, ignore_errors=True)
            shutil.copytree(build_cache_dir, llvm_helper.llvm_build_dir)

        apply_ok, apply_log = llvm_helper.apply(pt["patch"])
        check_more_res = None
        check_more_log = None
        if apply_ok:
            try:
                res_build, reason_build = env.build()
                if not res_build:
                    check_more_res = False
                    check_more_log = reason_build
                else:
                    check_more_res, check_more_log = llvm_helper.verify_test_group(
                        repro=False,
                        input=dev_tests,
                        type=dataset_data["bug_type"],
                    )
            except Exception as e:
                check_more_res = False
                check_more_log = str(e)

        retest_results.append(
            {
                "func_name": pt["func_name"],
                "file_path": pt["file_path"],
                "source_check_fast_pass": pt["source_check_fast_pass"],
                "source_check_full_pass": pt["source_check_full_pass"],
                "patch_apply": {"ok": apply_ok, "log": apply_log},
                "check_more": {"ok": check_more_res, "log": check_more_log},
            }
        )

    any_pass = any(r["check_more"]["ok"] for r in retest_results)
    log_payload = {
        "patch_source_file": patch_path,
        "patch_issue_id": patch_issue_id,
        "patch_timestamp": patch_timestamp,
        "test_issue_id": test_issue_id,
        "pass": any_pass,
        "retest_results": retest_results,
    }

    try:
        result = env.dump(log_payload)
    except Exception as e:
        result = {"error": f"Failed to dump env: {e}", "log": log_payload}

    write_json(out_path, result)


def main() -> None:
    issue_ids, override = parse_args(sys.argv)
    patch_dir = DEFAULT_PATCH_DIR
    output_root = DEFAULT_RETEST_ROOT

    patch_items = resolve_patch_files(patch_dir, issue_ids)
    if not patch_items:
        print("[WARN] No patch files to process")
        return

    os.makedirs(output_root, exist_ok=True)
    print(f"[INFO] Patch source dir: {patch_dir}")
    print(f"[INFO] Retest output root: {output_root}")

    for patch_issue_id, patch_path, patch_timestamp in patch_items:
        retest_patch(
            patch_issue_id,
            patch_path,
            patch_timestamp,
            output_root,
            override=override,
        )


if __name__ == "__main__":
    main()
