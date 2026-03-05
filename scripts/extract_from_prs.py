#!/usr/bin/env python3
"""
Extract source code changes and test case changes from InstCombine PRs.
Fetches diffs/patches from GitHub, extracts individual test functions
using llvm-extract, and saves structured data to dataset-patch/.

Usage:
    python3 scripts/extract_from_prs.py [--limit N] [--llvm-extract PATH]
"""

import os
import json
import urllib.request
import time
import re
import sys
import argparse
import subprocess
from unidiff import PatchSet

REPO = "llvm/llvm-project"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
OUTPUT_DIR = os.path.join(PROJECT_DIR, "dataset-patch")
PR_LIST_FILE = os.path.join(PROJECT_DIR, "instcombine_prs.json")

TESTNAME_PATTERN = re.compile(r"define\s+.+\s+@([.\w]+)\(")
RUNLINE_PATTERNS = [
    re.compile(r"; RUN: (.+)\|\s*(FileCheck .*)"),
    re.compile(r"; RUN: (.+)\\.*\|\s*(FileCheck .*)"),
]

SOURCE_PREFIXES = ("llvm/lib/", "llvm/include/")
TEST_PREFIX = "llvm/test/"

COMPONENT_PREFIXES = [
    "llvm/lib/Analysis/",
    "llvm/lib/Transforms/Scalar/",
    "llvm/lib/Transforms/Vectorize/",
    "llvm/lib/Transforms/Utils/",
    "llvm/lib/Transforms/IPO/",
    "llvm/lib/Transforms/",
    "llvm/lib/IR/",
]

TARGET_SUFFIXES = [
    "X86", "AArch64", "ARM", "Mips", "RISCV",
    "PowerPC", "LoongArch", "AMDGPU", "SystemZ", "Hexagon",
]

LLVM_EXTRACT_BIN = "llvm-extract-18"


def fetch_url(url, retries=3, timeout=60):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 403):
                wait = 30 * (attempt + 1)
                print(f"  Rate limited ({e.code}), waiting {wait}s...")
                time.sleep(wait)
            elif attempt < retries - 1:
                time.sleep(3)
            else:
                print(f"  HTTP error {e.code}: {e}")
                return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                print(f"  Error: {e}")
                return None
    return None


def get_pr_diff(pr_number):
    url = f"https://github.com/{REPO}/pull/{pr_number}.diff"
    return fetch_url(url)


def get_pr_commit_sha(pr_number):
    """Get merge commit SHA from .patch URL header."""
    url = f"https://github.com/{REPO}/pull/{pr_number}.patch"
    content = fetch_url(url, timeout=30)
    if content:
        first_line = content.split("\n", 1)[0]
        match = re.match(r"From\s+([0-9a-f]{40})\s+", first_line)
        if match:
            return match.group(1)
    return None


def fetch_raw_file(commit_sha, file_path):
    """Fetch a file from raw.githubusercontent.com at a specific commit.
    Falls back to 'main' branch if the commit SHA is no longer accessible
    (common for squash-merged PRs whose original commits are GC'd).
    """
    url = f"https://raw.githubusercontent.com/{REPO}/{commit_sha}/{file_path}"
    content = fetch_url(url, timeout=30)
    if content is not None:
        return content
    # Fallback: try main branch
    url_main = f"https://raw.githubusercontent.com/{REPO}/main/{file_path}"
    return fetch_url(url_main, timeout=30)


def remove_target_suffix(path):
    for target in TARGET_SUFFIXES:
        path = path.removesuffix("/" + target)
    return path


def infer_components(file_paths):
    components = set()
    for path in file_paths:
        for prefix in COMPONENT_PREFIXES:
            if path.startswith(prefix):
                comp = (
                    path[len(prefix):]
                    .split("/")[0]
                    .removesuffix(".cpp")
                    .removesuffix(".h")
                )
                if comp:
                    if comp.startswith("VPlan") or comp.startswith("LoopVectoriz") or comp.startswith("VPRecipe"):
                        comp = "LoopVectorize"
                    if comp.startswith("ScalarEvolution"):
                        comp = "ScalarEvolution"
                    if comp.startswith("ConstantFold"):
                        comp = "ConstantFold"
                    if "AliasAnalysis" in comp:
                        comp = "AliasAnalysis"
                    if comp.startswith("Attributor"):
                        comp = "Attributor"
                    if path.startswith("llvm/lib/IR"):
                        comp = "IR"
                    components.add(comp)
                break
    return sorted(components)


def extract_test_names_from_diff(patched_file):
    """Extract test function names touched by diff hunks."""
    test_names = set()
    for hunk in patched_file:
        matched = re.search(TESTNAME_PATTERN, hunk.section_header)
        if matched:
            test_names.add(matched.group(1))
        for line in hunk.target:
            for match in re.findall(TESTNAME_PATTERN, str(line)):
                test_names.add(match.strip())
    return test_names


def extract_commands_from_file(test_file_content):
    """Extract RUN line commands from a test file, merging all check-prefixes."""
    check_prefixes_raw = (
        re.findall(r"--check-prefixes=([^\s]+)", test_file_content)
        + re.findall(r"--check-prefix=([^\s]+)", test_file_content)
    )
    check_prefixes_all = []
    for raw in check_prefixes_raw:
        for pfx in raw.strip().split(","):
            if pfx not in check_prefixes_all:
                check_prefixes_all.append(pfx)

    commands = []
    for pattern in RUNLINE_PATTERNS:
        for match in re.findall(pattern, test_file_content):
            check_prefixes_curr = re.findall(r"(--check-prefix[^\s]+)", match[1])
            if check_prefixes_curr and check_prefixes_all:
                merged = f'--check-prefixes={",".join(check_prefixes_all)}'
                cmd = [match[0].strip(), match[1].strip().replace(check_prefixes_curr[0], merged)]
            else:
                cmd = [match[0].strip(), match[1].strip()]
            if cmd not in commands:
                commands.append(cmd)
    return commands


def trim_to_function_end(text):
    """Trim llvm-extract output to only keep content up to function closing brace.

    Strips trailing '; Function Attrs:', 'declare', 'attributes' lines
    that llvm-extract emits for intrinsics.
    """
    lines = text.splitlines()
    func_end = len(lines) - 1

    # Find the closing brace of the function: a line that is just '}'
    # (ignoring CHECK comment lines which may contain braces).
    in_func = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("define "):
            in_func = True
            continue
        if not in_func:
            continue
        if stripped.startswith(";"):
            continue
        if stripped == "}":
            func_end = i
            break

    return "\n".join(lines[: func_end + 1])


def extract_single_test(test_file_content, test_name, llvm_extract_bin):
    """
    Extract a single test function's test_body and source_program.
    Mirrors the logic from postfix_extract.py, but strips trailing
    declarations/attributes to match dataset/ format.
    """
    try:
        raw_extract = subprocess.check_output(
            [llvm_extract_bin, f"--func={test_name}", "-S", "-"],
            input=test_file_content.encode(),
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    full_extract = raw_extract.removeprefix(
        "; ModuleID = '<stdin>'\nsource_filename = \"<stdin>\"\n"
    ).removeprefix("\n")

    source_program = trim_to_function_end(full_extract) + "\n"

    extract_lines = full_extract.splitlines()
    test_body_lines = []
    found_function_def = False
    func_def_idx = 0

    for i in range(len(extract_lines)):
        test_body_lines.append(extract_lines[i])
        if test_name in extract_lines[i] and "define " in extract_lines[i]:
            func_def_idx = i
            break

    for file_line in test_file_content.splitlines():
        if file_line.strip() == extract_lines[func_def_idx].strip():
            found_function_def = True
            continue
        if found_function_def and file_line.strip().startswith("; "):
            test_body_lines.append(file_line)
        elif found_function_def:
            test_body_lines.extend(extract_lines[func_def_idx + 1:])
            break

    test_body = trim_to_function_end("\n".join(test_body_lines))
    return {"test_name": test_name, "test_body": test_body, "source_program": source_program}


def parse_and_extract(diff_text, commit_sha, llvm_extract_bin):
    """Parse diff text and extract full test case bodies."""
    try:
        patchset = PatchSet(diff_text)
    except Exception as e:
        print(f"  Failed to parse diff: {e}")
        return None

    source_files_p = []
    test_files_p = []

    for f in patchset:
        path = f.path
        if path.startswith(SOURCE_PREFIXES[0]) or path.startswith(SOURCE_PREFIXES[1]):
            source_files_p.append(f)
        elif TEST_PREFIX in path:
            test_files_p.append(f)

    source_patch = "".join(str(f) for f in source_files_p)
    test_patch = "".join(str(f) for f in test_files_p)

    source_file_paths = [f.path for f in source_files_p]
    test_file_paths = [f.path for f in test_files_p]

    lit_test_dirs = set()
    for path in test_file_paths:
        lit_test_dirs.add(remove_target_suffix(os.path.dirname(path)))

    components = infer_components(source_file_paths)

    # Source code hints
    source_lineno = {}
    source_funcname = {}
    for f in source_files_p:
        line_ranges = []
        func_sections = set()
        for hunk in f:
            src_lines = list(hunk.source_lines())
            if src_lines:
                min_l = min(l.source_line_no for l in src_lines)
                max_l = max(l.source_line_no for l in src_lines)
                line_ranges.append([min_l, max_l])
            if hunk.section_header:
                fm = re.search(r"(\w[\w:~]*)\s*\(", hunk.section_header)
                if fm:
                    func_sections.add(fm.group(1))
        if line_ranges:
            source_lineno[f.path] = line_ranges
        if func_sections:
            source_funcname[f.path] = sorted(func_sections)

    is_single_file = len(source_files_p) == 1
    total_funcs = sum(len(v) for v in source_funcname.values())
    is_single_func = is_single_file and total_funcs == 1

    # Extract test cases per test file
    tests = []
    for pf in test_files_p:
        test_names = extract_test_names_from_diff(pf)
        if not test_names:
            continue

        test_file_content = fetch_raw_file(commit_sha, pf.path)
        if not test_file_content:
            print(f"  Could not fetch {pf.path}")
            continue

        commands = extract_commands_from_file(test_file_content)

        subtests = []
        for tname in sorted(test_names):
            result = extract_single_test(test_file_content, tname, llvm_extract_bin)
            if result:
                subtests.append(result)

        if subtests:
            tests.append({
                "file": pf.path,
                "commands": commands,
                "tests": subtests,
            })

    return {
        "components": components,
        "lit_test_dir": sorted(lit_test_dirs),
        "source_patch": source_patch,
        "test_patch": test_patch,
        "source_files": source_file_paths,
        "test_files": test_file_paths,
        "tests": tests,
        "has_test_changes": len(tests) > 0,
        "has_source_changes": len(source_files_p) > 0,
        "hints": {
            "components": components,
            "bug_location_lineno": source_lineno,
            "bug_location_funcname": source_funcname,
        },
        "properties": {
            "is_single_file_fix": is_single_file,
            "is_single_func_fix": is_single_func,
        },
    }


def extract_pr(pr_number, pr_title, llvm_extract_bin):
    output_path = os.path.join(OUTPUT_DIR, f"{pr_number}.json")
    if os.path.exists(output_path):
        return "skip"

    commit_sha = get_pr_commit_sha(pr_number)
    if not commit_sha:
        print(f"  Could not get commit SHA for PR #{pr_number}")
        return "fail"

    diff_text = get_pr_diff(pr_number)
    if not diff_text:
        return "fail"

    parsed = parse_and_extract(diff_text, commit_sha, llvm_extract_bin)
    if parsed is None:
        return "fail"

    metadata = {
        "pr_number": pr_number,
        "pr_url": f"https://github.com/{REPO}/pull/{pr_number}",
        "title": pr_title,
        "commit_sha": commit_sha,
        "components": parsed["components"],
        "lit_test_dir": parsed["lit_test_dir"],
        "hints": parsed["hints"],
        "source_patch": parsed["source_patch"],
        "test_patch": parsed["test_patch"],
        "source_files": parsed["source_files"],
        "test_files": parsed["test_files"],
        "tests": parsed["tests"],
        "has_test_changes": parsed["has_test_changes"],
        "has_source_changes": parsed["has_source_changes"],
        "properties": parsed["properties"],
    }

    with open(output_path, "w") as f:
        json.dump(metadata, f, indent=2)

    test_count = sum(len(t["tests"]) for t in parsed["tests"])
    return f"ok({test_count} tests)"


def main():
    parser = argparse.ArgumentParser(description="Extract InstCombine PR patches")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of PRs (0=all)")
    parser.add_argument("--llvm-extract", default=LLVM_EXTRACT_BIN, help="Path to llvm-extract")
    args = parser.parse_args()

    try:
        subprocess.check_output([args.llvm_extract, "--version"], stderr=subprocess.STDOUT)
    except FileNotFoundError:
        print(f"Error: {args.llvm_extract} not found. Install it or use --llvm-extract PATH")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(PR_LIST_FILE) as f:
        prs = json.load(f)

    total = len(prs)
    if args.limit > 0:
        prs = prs[:args.limit]

    print(f"Total PRs in list: {total}")
    print(f"PRs to process: {len(prs)}")
    print(f"Output: {OUTPUT_DIR}")
    print(f"llvm-extract: {args.llvm_extract}")
    print("=" * 70)

    success = 0
    failed = 0
    skipped = 0

    for i, pr in enumerate(prs):
        pr_number = pr["number"]
        pr_title = pr["title"]

        result = extract_pr(pr_number, pr_title, args.llvm_extract)

        if result == "skip":
            skipped += 1
            continue
        elif result == "fail":
            failed += 1
            print(f"[{i+1}/{len(prs)}] FAIL  PR #{pr_number}: {pr_title[:60]}")
        else:
            success += 1
            print(f"[{i+1}/{len(prs)}] {result:15s} PR #{pr_number}: {pr_title[:55]}")

        time.sleep(0.2)

    print("=" * 70)
    print(f"Done! Success: {success}, Failed: {failed}, Skipped: {skipped}")
    print(f"Total files in {OUTPUT_DIR}: {len(os.listdir(OUTPUT_DIR))}")


if __name__ == "__main__":
    main()
