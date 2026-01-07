#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI
from tqdm import tqdm

sys.path.append(os.path.join(os.path.dirname(os.environ.get("LAB_DATASET_DIR", ".")), "scripts"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "scripts"))
import llvm_helper  # pyright: ignore[reportMissingImports]  # noqa: E402
from lab_env import Environment as Env  # pyright: ignore[reportMissingImports]  # noqa: E402

DATASET_DIR = os.environ.get("LAB_DATASET_DIR", "dataset")
OUTPUT_RESULTS_PATH = os.environ.get("LAB_LOCALIZE_OUTPUT", os.path.join(os.path.dirname(__file__), "localize-result.json"))

DEFAULT_LLVM_ROOT = os.environ.get("LAB_LLVM_DIR", "utils/llvm/llvm-project")
LLVM_ROOT = os.environ.get("LAB_LOCALIZE_LLVM_ROOT", DEFAULT_LLVM_ROOT)

TOKEN = os.environ.get("LAB_LLM_TOKEN")
BASE_URL = os.environ.get("LAB_LLM_URL")
MODEL_NAME = os.environ.get("LAB_LLM_MODEL", "qwen/qwen3-235b-a22b-2507")
TEMPERATURE = float(os.environ.get("LAB_LLM_TEMP", "0.8"))

BASEMODEL_CUTOFF = os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")
MAX_BUILD_JOBS = int(os.environ.get("LAB_MAX_BUILD_JOBS", os.cpu_count()))

MAX_FILE_CHARS = int(os.environ.get("LAB_LOCALIZE_MAX_FILE_CHARS", "200000"))
MAX_ISSUE_DESC_CHARS = int(os.environ.get("LAB_LOCALIZE_MAX_ISSUE_DESC_CHARS", "20000"))
MAX_DEBUG_CHARS = int(os.environ.get("LAB_LOCALIZE_MAX_DEBUG_CHARS", "5000"))

ENABLE_OPT_DEBUG = os.environ.get("LAB_LOCALIZE_ENABLE_OPT_DEBUG", "ON") == "ON"
BUILD_BEFORE_OPT_DEBUG = os.environ.get("LAB_LOCALIZE_BUILD_BEFORE_OPT_DEBUG", "ON") == "ON"
OPT_DEBUG_TIMEOUT = int(os.environ.get("LAB_LOCALIZE_OPT_DEBUG_TIMEOUT", "30"))

CANDIDATE_FILES = [
    "llvm/lib/Transforms/InstCombine/InstCombineCalls.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineLoadStoreAlloca.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp",
    "llvm/lib/Transforms/InstCombine/InstructionCombining.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineAddSub.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineCasts.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineMulDivRem.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineShifts.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineCompares.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineNegator.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineSimplifyDemanded.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineAtomicRMW.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombinePHI.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineVectorOps.cpp",
]

SYS_PROMPT_FILE = (
    "You are an LLVM maintainer. Users have reported a case where LLVM "
    "failed to optimize a specific program. You are locating the position "
    "of the faulty code (which source file likely needs changes)."
)

SYS_PROMPT_FUNC = (
    "You are an LLVM maintainer. Users have reported a case where LLVM "
    "failed to optimize a specific program. You are locating the specific "
    "function within a file that needs to be modified to implement the missing optimization."
)

if TOKEN:
    if BASE_URL:
        client = OpenAI(api_key=TOKEN, base_url=BASE_URL)
    else:
        client = OpenAI(api_key=TOKEN)
else:
    client = OpenAI()

TEXT_BLOCK_PATTERN = re.compile(r"```text(.*?)```", re.DOTALL)

FORMAT_ISSUE_DESC_FROM_PROGRAMS = """
The following program reveals the missed optimization.
The source program is
```llvm
{source_program}
```

However, current LLVM cannot optimize the source program. The expected optimized program is
```llvm
{expect_optimized_program}
```
"""


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


def _truncate(s: str | None, max_chars: int) -> str:
    if not s:
        return ""
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "\n<Truncated>..."


def build_file_list_prompt() -> str:
    return "\n".join([os.path.basename(f) for f in CANDIDATE_FILES])


def extract_text_block_lines(model_output: str) -> list[str] | None:
    m = TEXT_BLOCK_PATTERN.search(model_output or "")
    if not m:
        return None
    body = m.group(1).strip()
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    return lines or None


def extract_pred_files_from_model_output(model_output: str) -> list[str] | None:
    lines = extract_text_block_lines(model_output)
    if not lines:
        return None
    return lines[:3]


def extract_pred_funcs_from_model_output(model_output: str) -> list[str] | None:
    lines = extract_text_block_lines(model_output)
    if not lines:
        return None
    return lines[:3]


def get_test_case_from_env(env: Env) -> tuple[str, str, str, dict] | None:
    for test_file in env.get_tests():
        for test in test_file["tests"]:
            if test["current_optimized_program"].strip() != test["expect_optimized_program"].strip():
                return (
                    test["source_program"],
                    test["current_optimized_program"],
                    test["expect_optimized_program"],
                    test_file,
                )
    return None


def load_gold_file_and_funcs(issue_id: str, dataset_dir: str = DATASET_DIR) -> tuple[str | None, list[str]]:
    json_path = os.path.join(dataset_dir, f"{issue_id}.json")
    if not os.path.exists(json_path):
        return None, []

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, []

    hints = data.get("hints")
    if not isinstance(hints, dict):
        return None, []

    bug_loc = hints.get("bug_location_funcname")
    if not isinstance(bug_loc, dict):
        return None, []

    keys = list(bug_loc.keys())
    if not keys:
        return None, []

    gold_file = keys[0].strip() if isinstance(keys[0], str) else None
    if not gold_file:
        return None, []

    func_names = bug_loc.get(gold_file, [])
    if isinstance(func_names, list):
        gold_funcs = [x.strip() for x in func_names if isinstance(x, str) and x.strip()]
    elif isinstance(func_names, str):
        gold_funcs = [func_names.strip()] if func_names.strip() else []
    else:
        gold_funcs = []

    return gold_file, gold_funcs


def get_full_path_rel_under_llvm_root(filename: str, llvm_root: str = LLVM_ROOT) -> str | None:
    basename = os.path.basename(filename)
    llvm_path = Path(llvm_root)
    if not llvm_path.exists():
        return None

    matches: list[Path] = []
    for p in llvm_path.rglob(basename):
        if p.is_file():
            matches.append(p)
    if not matches:
        return None

    matches.sort(key=lambda x: str(x))
    rel = matches[0].relative_to(llvm_path)
    return str(rel).replace("\\", "/")


def read_file_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def format_issue_desc_from_programs(
    source_program: str, current_optimized_program: str, expect_optimized_program: str
) -> str:
    desc = FORMAT_ISSUE_DESC_FROM_PROGRAMS.format(
        source_program=source_program,
        current_optimized_program=current_optimized_program,
        expect_optimized_program=expect_optimized_program,
    )
    return _truncate(desc, MAX_ISSUE_DESC_CHARS)


def get_opt_debug_output(source_program: str, test_file: dict) -> str | None:
    try:
        commands = test_file.get("commands", [])
        if not commands:
            return None

        opt_cmd = commands[0][0] if isinstance(commands[0], list) else commands[0]
        if not isinstance(opt_cmd, str) or not opt_cmd.strip():
            return None

        with tempfile.NamedTemporaryFile(mode="w", suffix=".ll", delete=False) as f:
            f.write(source_program)
            temp_file = f.name

        try:
            opt_path = os.path.join(llvm_helper.llvm_build_dir, "bin/opt")
            opt_cmd_parts = opt_cmd.replace("< ", " ").replace("%s", temp_file).split()

            for i, arg in enumerate(opt_cmd_parts):
                if arg == "opt" or (arg.endswith("/opt") and "opt" in arg):
                    opt_cmd_parts[i] = opt_path
                    break

            if "-S" in opt_cmd_parts:
                s_idx = opt_cmd_parts.index("-S")
                opt_cmd_parts.insert(s_idx, "-debug")
            else:
                opt_cmd_parts.append("-debug")

            result = subprocess.run(
                opt_cmd_parts,
                capture_output=True,
                text=True,
                timeout=OPT_DEBUG_TIMEOUT,
            )
            return result.stderr if result.stderr else None
        finally:
            try:
                os.unlink(temp_file)
            except Exception:
                pass
    except Exception:
        return None


def call_model(
    prompt: str,
    *,
    sys_prompt: str,
    model: str = MODEL_NAME,
    temperature: float = TEMPERATURE,
    timeout: int = 300,
) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        timeout=timeout,
    )
    return resp.choices[0].message.content or ""


def build_file_localize_prompt(issue_desc: str, file_list_tree: str, debug_output: str | None) -> str:
    debug_context = ""
    if debug_output:
        debug_context = f"""

**Debug Output Context**: When running the `opt` command on the source program, the following debug output was generated. This output may reveal which optimization passes, analyses, or code paths are involved in processing this program. Use this information to help identify which files are most likely responsible for the missed optimization:

```
{_truncate(debug_output, MAX_DEBUG_CHARS)}
```
"""

    analyze_section = "### 1. Analyze\n**Content**: Provide a deep analysis of the missed optimization. Explain:"
    if debug_output:
        analyze_section += "\n- **Context**: Consider the debug output provided above, which shows the actual execution trace of LLVM optimization passes when processing this program."
    analyze_section += """
- **What** the issue is (what optimization is missing or failing).
- **Why** it happens in the current LLVM implementation (e.g., which patterns, analyses, or passes are insufficient or incorrect).
- **Impact** on performance or correctness.
"""

    verify_section = "### 3. Verify\n**Content**: Justify why these three specific files are the most appropriate locations for the change:"
    if debug_output:
        verify_section += "\n- **Evidence from Debug Output**: If the debug output above mentions specific passes or components, explain how this supports your file selection."
    verify_section += """
- Describe the responsibilities and typical transformations implemented in each file.
- Explain the ranking order (why the first file is more likely than the second, etc.).
"""

    prompt = f"""
LLVM is currently unable to perform this optimization successfully. Your task is to identify which three files below are most likely to be modified to implement the missing optimization. Use the exact Markdown section headings and structure provided.

{issue_desc}{debug_context}

You must choose exactly three files from this list (ranked by likelihood):

{file_list_tree}

--

{analyze_section}

### 2. Propose positions
**Content**: You must output exactly three file names from the list above, ranked by likelihood (most likely first). Do not explain here; only output the chosen file names exactly as written in the list, one per line.

{verify_section}

### 4. Submit
**Content**: Provide the final answer by outputting exactly three file names (one per line), enclosed in a Markdown code block with language tag `text`, like:

```text
InstCombineXXX.cpp
InstCombineYYY.cpp
InstCombineZZZ.cpp
```
""".strip()
    return prompt


def build_func_localize_prompt(pred_file: str, full_path_rel: str, issue_desc: str, file_text: str) -> str:
    file_text = _truncate(file_text, MAX_FILE_CHARS)
    return f"""
LLVM is currently unable to perform this optimization successfully. We have identified that the file `{pred_file}` (full path: `{full_path_rel}`) is likely to contain the code that needs modification.

{issue_desc}

Below is the complete content of this file:
```cpp
{file_text}
```

### 1. Analyze
**Content**: Carefully read the file content and map functions to their likely responsibilities. Relate IR patterns in the source vs. expected optimized programs to the functions that typically handle those transformations.

### 2. Propose
**Content**: Identify the top three functions most likely responsible for the missed optimization. Give the function names exactly as they appear in the file. Do not include explanations in the final answer.

### 3. Submit
**Content**: Output exactly three function names (one per line), ranked by likelihood, wrapped in a Markdown code block with language tag `text`, like:

```text
InstCombinerImpl::visitMul
InstCombinerImpl::visitAdd
InstCombinerImpl::visitSub
```
""".strip()


def compute_file_correct(pred_files: list[str] | None, gold_file: str | None) -> bool | None:
    if pred_files is None or gold_file is None:
        return None

    for pf in pred_files:
        if pf and pf in gold_file:
            return True

    for pf in pred_files:
        rel = get_full_path_rel_under_llvm_root(pf, LLVM_ROOT)
        if rel and rel == gold_file:
            return True

    return False


def compute_func_correct(
    pred_funcs: dict[str, list[str] | None],
    gold_file: str | None,
    gold_funcs: list[str],
) -> bool | None:
    if not gold_file or not gold_funcs:
        return None

    for pred_file, funcs in pred_funcs.items():
        if not funcs:
            continue

        rel = get_full_path_rel_under_llvm_root(pred_file, LLVM_ROOT)
        if not rel or rel != gold_file:
            continue

        for pf in funcs:
            for gf in gold_funcs:
                if pf in gf or gf.endswith("::" + pf):
                    return True

    return False


def main() -> None:
    issue_ids, override = parse_args(sys.argv)

    try:
        dataset_files = [
            f
            for f in os.listdir(DATASET_DIR)
            if os.path.isfile(os.path.join(DATASET_DIR, f)) and f.endswith(".json")
        ]
    except FileNotFoundError:
        print(f"[ERROR] Dataset directory {DATASET_DIR} not found")
        return

    if issue_ids is not None:
        wanted = set(issue_ids)
        dataset_files = [f for f in dataset_files if os.path.splitext(f)[0] in wanted]
        missing = sorted(wanted - {os.path.splitext(f)[0] for f in dataset_files})
        if missing:
            print(f"[WARN] These issues not found in {DATASET_DIR}: {missing}")

    results: dict[str, dict[str, Any]] = {}
    if os.path.exists(OUTPUT_RESULTS_PATH):
        try:
            with open(OUTPUT_RESULTS_PATH, "r", encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict):
                results = old
        except Exception as e:
            print(f"[WARN] Failed to read existing {OUTPUT_RESULTS_PATH}: {e}")

    file_list_tree = build_file_list_prompt()

    for fname in tqdm(dataset_files, desc="Processing issues"):
        issue_id, _ext = os.path.splitext(fname)

        if (not override) and (issue_id in results):
            print(f"\n[SKIP] {issue_id} already exists in {OUTPUT_RESULTS_PATH} (use -f to override)")
            continue

        print(f"\n[INFO] Processing issue {issue_id}...")

        json_path = os.path.join(DATASET_DIR, fname)
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[ERROR] Failed to read/parse {json_path}: {e}")
            continue

        try:
            env = Env(issue_id, BASEMODEL_CUTOFF, max_build_jobs=MAX_BUILD_JOBS)
            env.reset()
        except Exception as e:
            print(f"[ERROR] Failed to reset env for {issue_id}: {e}")
            continue

        test_case = get_test_case_from_env(env)
        if not test_case:
            print(f"[SKIP] No differing test case found for {issue_id}")
            continue

        source_program, current_optimized_program, expect_optimized_program, test_file = test_case
        issue_desc = format_issue_desc_from_programs(
            source_program, current_optimized_program, expect_optimized_program
        )

        gold_file, gold_funcs = load_gold_file_and_funcs(issue_id, DATASET_DIR)
        print(f"[INFO] Gold: file={gold_file}, funcs={gold_funcs}")

        debug_output = None
        if ENABLE_OPT_DEBUG:
            if BUILD_BEFORE_OPT_DEBUG:
                print(f"[INFO] Building LLVM for {issue_id}...")
                try:
                    build_res, _build_log = env.build()
                    if not build_res:
                        print(f"[WARN] Build failed for {issue_id}, debug output disabled for this issue")
                    else:
                        debug_output = get_opt_debug_output(source_program, test_file)
                except Exception as e:
                    print(f"[WARN] Build/debug failed for {issue_id}: {e}")
            else:
                debug_output = get_opt_debug_output(source_program, test_file)

            if debug_output:
                print(f"[INFO] Debug output obtained (len={len(debug_output)})")
            else:
                print("[INFO] No debug output")

        file_prompt = build_file_localize_prompt(issue_desc, file_list_tree, debug_output)
        print(f"[PROMPT:file_localize:{issue_id}]\n{file_prompt}\n")
        print(f"[INFO] Calling model (file localize) for {issue_id}...")
        try:
            file_model_output = call_model(file_prompt, sys_prompt=SYS_PROMPT_FILE)
            pred_files = extract_pred_files_from_model_output(file_model_output)
        except Exception as e:
            print(f"[ERROR] File-localize model call failed for {issue_id}: {e}")
            pred_files = None

        print(f"[INFO] Predicted files: {pred_files}")
        file_correct = compute_file_correct(pred_files, gold_file)

        pred_funcs: dict[str, list[str] | None] = {}

        if not pred_files:
            print(f"[SKIP] No predicted files for {issue_id}; skipping func localization")
        else:
            for pred_file in pred_files:
                print(f"[INFO] Func-localize within file: {pred_file}")

                full_rel = get_full_path_rel_under_llvm_root(pred_file, LLVM_ROOT)
                if not full_rel:
                    print(f"[WARN] Cannot resolve {pred_file} under LLVM_ROOT={LLVM_ROOT}")
                    pred_funcs[pred_file] = None
                    continue

                full_abs = os.path.join(LLVM_ROOT, full_rel)
                file_text = read_file_text(full_abs)
                if file_text is None:
                    print(f"[WARN] Failed to read {full_rel}")
                    pred_funcs[pred_file] = None
                    continue

                func_prompt = build_func_localize_prompt(pred_file, full_rel, issue_desc, file_text)
                print(f"[PROMPT:func_localize:{issue_id}:{pred_file}]\n{func_prompt}\n")
                print(f"[INFO] Calling model (func localize) for {pred_file}...")
                try:
                    func_model_output = call_model(func_prompt, sys_prompt=SYS_PROMPT_FUNC)
                    funcs = extract_pred_funcs_from_model_output(func_model_output)
                    pred_funcs[pred_file] = funcs
                    print(f"[INFO] Predicted functions for {pred_file}: {funcs}")
                except Exception as e:
                    print(f"[ERROR] Func-localize model call failed for {pred_file}: {e}")
                    pred_funcs[pred_file] = None

        func_correct = compute_func_correct(pred_funcs, gold_file, gold_funcs)

        results[issue_id] = {
            "pred_files": pred_files,
            "pred_funcs": pred_funcs,
            "gold_file": gold_file,
            "gold_funcs": gold_funcs,
            "file_correct": file_correct,
            "func_correct": func_correct,
        }

        print(f"[INFO] Saved {issue_id}: file_correct={file_correct}, func_correct={func_correct}")

        try:
            with open(OUTPUT_RESULTS_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] Failed to write intermediate results to {OUTPUT_RESULTS_PATH}: {e}")

    print(f"\n[INFO] Writing results to {OUTPUT_RESULTS_PATH}...")
    with open(OUTPUT_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"[INFO] Results saved to {OUTPUT_RESULTS_PATH}")
    print(f"[INFO] Total processed: {len(results)} issues")


if __name__ == "__main__":
    main()
