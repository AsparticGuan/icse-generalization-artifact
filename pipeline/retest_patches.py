#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Retest existing patches on dataset test cases.

Default behavior:
  - Read patches from results/generate/fixes-gemini-3-cot-iter4-orig
  - Only process patches with fast_check_pass == true
  - If full_check_pass == false, use fast_full_mismatch_patch
  - If full_check_pass == true, use patch
  - For each patch (e.g., 85250-orig.json), use dataset/85250.json if present
  - Apply patch, then run env.check_fast() to verify

Usage:
  python pipeline/retest_patches.py
  python pipeline/retest_patches.py 85250-orig,76128-orig -f

Env overrides:
  LAB_PATCH_DIR   - directory containing patch JSON files
  LAB_RETEST_DIR  - output directory for retest logs
"""

import json
import os
import shutil
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))

DEFAULT_PATCH_DIR = os.environ.get(
    "LAB_PATCH_DIR",
    os.path.join(
        _PROJECT_ROOT, "results", "generate", "fixes-gemini-3-cot-iter4-orig"
    ),
)
DEFAULT_RETEST_DIR = os.environ.get(
    "LAB_RETEST_DIR",
    os.path.join(
        _PROJECT_ROOT,
        "results",
        "generate",
        "retest-fixes-gemini-3-cot-iter4-orig",
    ),
)

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
from lab_env import Environment as Env  # pyright: ignore[reportMissingImports]  # noqa: E402

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
    if len(argv) >= 2:
        raw = argv[1].strip()
        if raw:
            issue_ids = [x.strip() for x in raw.split(",") if x.strip()]
    if len(argv) >= 3 and argv[2] == "-f":
        override = True
    return issue_ids, override


def resolve_patch_files(
    patch_dir: str, issue_ids: list[str] | None
) -> list[tuple[str, str]]:
    patch_items: list[tuple[str, str]] = []
    if issue_ids is None:
        if not os.path.isdir(patch_dir):
            print(f"[ERROR] Patch dir not found: {patch_dir}")
            return []
        for fname in sorted(os.listdir(patch_dir)):
            if not fname.endswith(".json"):
                continue
            issue_id = os.path.splitext(fname)[0]
            patch_items.append((issue_id, os.path.join(patch_dir, fname)))
        return patch_items

    for raw in issue_ids:
        issue_id = raw.strip()
        if issue_id.endswith(".json"):
            issue_id = issue_id[:-5]
        candidates = [
            f"{issue_id}.json",
            f"{issue_id}-orig.json",
        ]
        if issue_id.endswith("-orig"):
            base = issue_id[: -len("-orig")]
            candidates.insert(0, f"{issue_id}.json")
            candidates.append(f"{base}.json")

        found = None
        for cand in candidates:
            cand_path = os.path.join(patch_dir, cand)
            if os.path.exists(cand_path):
                found = cand_path
                break
        if not found:
            print(f"[WARN] Patch file not found for {raw} in {patch_dir}")
            continue
        patch_items.append((os.path.splitext(os.path.basename(found))[0], found))
    return patch_items


def resolve_test_issue_id(patch_issue_id: str, dataset_dir: str) -> str | None:
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


def write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def retest_patch(
    patch_issue_id: str,
    patch_path: str,
    output_dir: str,
    *,
    override: bool = False,
) -> None:
    out_path = os.path.join(output_dir, f"{patch_issue_id}.json")
    if not override and os.path.exists(out_path):
        print(f"[SKIP] {patch_issue_id} exists in {output_dir} (use -f to override)")
        return

    print(f"[INFO] Retesting patch {patch_issue_id} from {patch_path}")
    try:
        with open(patch_path, "r", encoding="utf-8") as f:
            patch_data = json.load(f)
    except Exception as e:
        write_json(out_path, {"error": f"Failed to read patch JSON: {e}"})
        return

    fast_check_pass = patch_data.get("fast_check_pass")
    if fast_check_pass is not True:
        print(
            f"[SKIP] {patch_issue_id} fast_check_pass != true in {patch_path}"
        )
        return

    full_check_pass = patch_data.get("full_check_pass")
    patch_key = "patch"
    if full_check_pass is False:
        patch_key = "fast_full_mismatch_patch"

    patch = patch_data.get(patch_key)
    if not isinstance(patch, str) or not patch.strip():
        write_json(
            out_path,
            {"error": f"Patch is missing or empty (field: {patch_key})"},
        )
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

    env.reset()
    if os.path.exists(build_cache_dir):
        shutil.rmtree(llvm_helper.llvm_build_dir, ignore_errors=True)
        shutil.copytree(build_cache_dir, llvm_helper.llvm_build_dir)
    apply_ok, apply_log = llvm_helper.apply(patch)
    check_more_res = None
    check_more_log = None
    if apply_ok:
        try:
            check_more_res, check_more_log = env.check_fast(skip_build=False)
        except Exception as e:
            check_more_res = False
            check_more_log = str(e)

    log_payload = {
        "patch_source_file": patch_path,
        "patch_issue_id": patch_issue_id,
        "test_issue_id": test_issue_id,
        "source_fast_check_pass": fast_check_pass,
        "patch_apply": {"ok": apply_ok, "log": apply_log},
        "check_more": {"ok": check_more_res, "log": check_more_log},
    }

    try:
        result = env.dump(log_payload)
    except Exception as e:
        result = {"error": f"Failed to dump env: {e}", "log": log_payload}

    write_json(out_path, result)


def main() -> None:
    issue_ids, override = parse_args(sys.argv)
    patch_dir = DEFAULT_PATCH_DIR
    output_dir = DEFAULT_RETEST_DIR

    patch_items = resolve_patch_files(patch_dir, issue_ids)
    if not patch_items:
        print("[WARN] No patch files to process")
        return

    os.makedirs(output_dir, exist_ok=True)
    for patch_issue_id, patch_path in patch_items:
        retest_patch(
            patch_issue_id, patch_path, output_dir, override=override
        )


if __name__ == "__main__":
    main()
