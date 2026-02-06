#!/usr/bin/env python3
import os
import re
import json
import sys
import subprocess
from pathlib import Path
from typing import Any
from openai import OpenAI

sys.path.append(os.path.join(os.path.dirname(os.environ.get("LAB_DATASET_DIR", ".")), "scripts"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "scripts"))
import llvm_helper  # pyright: ignore[reportMissingImports]  # noqa: E402
from lab_env import Environment as Env  # pyright: ignore[reportMissingImports]  # noqa: E402

DATASET_DIR = os.environ.get("LAB_DATASET_DIR", "dataset")
_DEFAULT_RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "results", "generalize")
_DEFAULT_MODEL_TAG = re.sub(r"[^A-Za-z0-9._-]+", "_", os.environ.get("LAB_LLM_MODEL", "model"))
OUTPUT_RESULTS_PATH = os.environ.get(
    "LAB_GENERALIZE_OUTPUT",
    os.path.join(_DEFAULT_RESULTS_DIR, f"{_DEFAULT_MODEL_TAG}.json"),
)

TOKEN = os.environ.get("LAB_LLM_TOKEN")
BASE_URL = os.environ.get("LAB_LLM_URL")
MODEL_NAME = os.environ.get("LAB_LLM_MODEL", "qwen/qwen3-235b-a22b-2507")
TEMPERATURE = float(os.environ.get("LAB_LLM_TEMP", "0.8"))
BASEMODEL_CUTOFF = os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")
MAX_BUILD_JOBS = int(os.environ.get("LAB_MAX_BUILD_JOBS", os.cpu_count()))

if TOKEN:
    if BASE_URL:
        client = OpenAI(api_key=TOKEN, base_url=BASE_URL)
    else:
        client = OpenAI(api_key=TOKEN)
else:
    client = OpenAI()

PROMPT_TEMPLATE = r"""
You are an expert LLVM compiler engineer, specializing in LLVM IR, InstCombine, and missed-optimization analysis.

I found a **new missed optimization in InstCombine** and currently have **only one LLVM IR example**.

Your tasks:
1) Understand the missed optimization from my single example.
2) Generalize it into a reusable pattern (preconditions, canonical form, legality constraints).
3) Generate many additional LLVM IR test cases that are semantically equivalent and should trigger the same transformation, including corner cases.
4) Summarize the pattern so it could be implemented as an InstCombine fold (or described as an Alive2/MLIR-style rewrite).
5) Optionally propose a plausible InstCombine implementation sketch (match + fold) and list pitfalls.

---

## How to expand examples (optimization-oriented guidance)
When generating more cases, expand along two main lines:

### (1) Semantically equivalent but structurally diverse IR variants
Goal: cover InstCombine pattern-matching blind spots by keeping semantics but changing shape.
Systematically vary:
- **Type/bitwidth forms**: different integer widths (including unusual widths), vectors, and any safe pointer-adjacent encodings (avoid introducing UB).
- **Expression isomorphisms**: commuted operands, reassociated trees where legal, equivalent bitwise identities, alternative opcode families that encode the same math, and “noise” operations that should be transparent (`add 0`, `xor 0`, `and -1`, `zext/trunc round-trips` when type-safe).
- **Use-def shape changes**: insert `select` or small CFG with `phi` to represent the same value flow; ensure legality and keep the fold local if possible.

### (2) Boundary conditions & context perturbations
Goal: validate “only under some constraints” and reveal why InstCombine might be conservative.
Systematically vary:
- **Constants and bit patterns**: `0/1/-1`, min/max, low-bit/high-bit masks, and structured constants like alternating-bit masks; include cases that sharpen or break preconditions.
- **Flags/semantic constraints**: `nuw/nsw/exact`, and any poison/undef interactions. Include pairs of cases that differ only by a flag or a `freeze`, to show when the rewrite becomes legal/illegal or when InstCombine should/shouldn’t fire.
- **Context realism**: add minimal surrounding IR (extra uses, extra blocks, simple caller/callee boundary) to see if matching fails due to non-canonical forms or interference.

Use these dimensions to generate grouped variants (e.g., “only bitwidth changes”, then “only flags changes”), so each new case remains attributable and easy to debug.

---

## Output format requirement (strict)
Return **ONLY** JSON (a single JSON object). No Markdown, no extra text.

## What I will provide
- The original LLVM IR snippet (minimal reproducer).
- The target triple/datalayout if relevant.
- What I expected InstCombine to do vs what it does today.
- Any constraints I already suspect (e.g., `nsw/nuw`, `exact`, `inbounds`, poison/undef considerations, fast-math flags, vector widths).

## What you must output (in JSON)
Include the following top-level keys (you may add more keys if useful):
- "summary"
- "before_after" (describe the rewrite)
- "preconditions" (legality constraints, including poison/undef/flags)
- "pattern" (generalized pattern description)
- "testcases" (an array of testcase objects; include both positive and negative cases)
- "implementation_notes" (InstCombine-oriented notes)
- "regression_test_notes" (how to test with opt/FileCheck)
- "clarifying_questions" (ask at most 3, only if truly needed)

Each testcase object must include at least:
- "name"
- "ir" (the input IR for this case)
- "expected_optimized_ir" (what InstCombine should produce, at least the key simplified instructions; full function preferred)
- "why" (one sentence)

---

## Here is my single example 
{source_code}
{expected_optmized_code}
""".strip()


def _extract_json_obj(text: str) -> dict | None:
    if not isinstance(text, str):
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _ensure_ptr_datalayout(ir: str) -> str:
    if "ptr" in ir and "target datalayout" not in ir:
        return 'target datalayout = "p:8:8:8"\n' + ir
    return ir


def _run_opt_instcombine(ir: str, timeout_s: int = 10) -> tuple[bool, str]:
    opt_path = os.path.join(llvm_helper.llvm_build_dir, "bin/opt")
    if not os.path.exists(opt_path):
        return False, f"[ERROR] opt not found at {opt_path}"
    try:
        res = subprocess.run(
            [opt_path, "--passes=instcombine", "-S"],
            input=ir.encode(),
            capture_output=True,
            timeout=timeout_s,
            check=True,
        )
        return True, res.stdout.decode()
    except Exception as e:
        return False, str(e)


def _alive2_check(src: str, tgt: str) -> tuple[bool, Any]:
    src = _ensure_ptr_datalayout(src)
    tgt = _ensure_ptr_datalayout(tgt)
    return llvm_helper.alive2_check(src, tgt, "-src-unroll=8 -tgt-unroll=8")


def _find_first_testcase(data: dict) -> tuple[dict, dict] | None:
    tests = data.get("tests", [])
    for test_file in tests:
        for test in test_file.get("tests", []):
            if test.get("source_program") and test.get("expect_optimized_program"):
                return test_file, test
    return None


def _load_first_dataset_item(dataset_dir: str) -> tuple[str, dict, dict, dict] | None:
    try:
        dataset_files = sorted(
            [
                f
                for f in os.listdir(dataset_dir)
                if os.path.isfile(os.path.join(dataset_dir, f)) and f.endswith(".json")
            ]
        )
    except FileNotFoundError:
        return None

    if not dataset_files:
        return None

    first_file = dataset_files[0]
    issue_id, _ext = os.path.splitext(first_file)
    json_path = os.path.join(dataset_dir, first_file)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    found = _find_first_testcase(data)
    if not found:
        return None
    test_file, test = found
    return issue_id, data, test_file, test


def _call_model(prompt: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": "You are an expert LLVM compiler engineer."},
            {"role": "user", "content": prompt},
        ],
        temperature=TEMPERATURE,
        timeout=300,
    )
    return resp.choices[0].message.content or ""


def main() -> None:
    os.makedirs(os.path.dirname(OUTPUT_RESULTS_PATH), exist_ok=True)
    loaded = _load_first_dataset_item(DATASET_DIR)
    if not loaded:
        print(f"[ERROR] Failed to load a test case from {DATASET_DIR}")
        return

    issue_id, data, _test_file, test = loaded
    source_program = test.get("source_program", "")
    expect_optimized_program = test.get("expect_optimized_program", "")
    if not source_program or not expect_optimized_program:
        print("[ERROR] source_program or expect_optimized_program missing")
        return

    prompt = PROMPT_TEMPLATE.format(
        source_code=source_program,
        expected_optmized_code=expect_optimized_program,
    )

    print(f"[INFO] Calling model for {issue_id}...")
    model_output = _call_model(prompt)
    parsed = _extract_json_obj(model_output)

    results: dict[str, Any] = {
        "issue_id": issue_id,
        "dataset_file": f"{issue_id}.json",
        "prompt": prompt,
        "model_output": model_output,
        "parsed": parsed,
        "validation": [],
    }

    if parsed is None:
        print("[ERROR] Failed to parse model output as JSON")
        with open(OUTPUT_RESULTS_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        return

    print(f"[INFO] Building LLVM for {issue_id}...")
    try:
        env = Env(issue_id, BASEMODEL_CUTOFF, max_build_jobs=MAX_BUILD_JOBS)
        env.reset()
        build_ok, build_log = env.build()
        results["build"] = {"ok": build_ok, "log": build_log}
        if not build_ok:
            print("[ERROR] Build failed; aborting validation")
            with open(OUTPUT_RESULTS_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            return
    except Exception as e:
        results["build"] = {"ok": False, "log": str(e)}
        print(f"[ERROR] Build failed: {e}")
        with open(OUTPUT_RESULTS_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        return

    testcases = parsed.get("testcases", [])
    if not isinstance(testcases, list):
        testcases = []

    for idx, tc in enumerate(testcases):
        if not isinstance(tc, dict):
            results["validation"].append(
                {"index": idx, "ok": False, "error": "testcase is not an object"}
            )
            continue

        name = tc.get("name", f"case_{idx}")
        src_ir = tc.get("ir", "")
        exp_ir = tc.get("expected_optimized_ir", "")
        if not isinstance(src_ir, str) or not isinstance(exp_ir, str) or not src_ir.strip():
            results["validation"].append(
                {"name": name, "ok": False, "error": "missing ir or expected_optimized_ir"}
            )
            continue

        opt_ok, opt_out = _run_opt_instcombine(src_ir)
        if not opt_ok:
            results["validation"].append(
                {"name": name, "ok": False, "error": opt_out}
            )
            continue

        a2_src_exp_ok, a2_src_exp_log = _alive2_check(src_ir, exp_ir)
        a2_opt_exp_ok, a2_opt_exp_log = _alive2_check(opt_out, exp_ir)
        a2_exp_opt_ok, a2_exp_opt_log = _alive2_check(exp_ir, opt_out)

        equivalent = a2_opt_exp_ok and a2_exp_opt_ok
        missed_optimization = a2_src_exp_ok and (not equivalent)

        results["validation"].append(
            {
                "name": name,
                "missed_optimization": missed_optimization,
                "source_to_expected_ok": a2_src_exp_ok,
                "opt_equivalent_expected": equivalent,
                "alive2": {
                    "src_to_expected": a2_src_exp_log,
                    "opt_to_expected": a2_opt_exp_log,
                    "expected_to_opt": a2_exp_opt_log,
                },
            }
        )

    with open(OUTPUT_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"[INFO] Results saved to {OUTPUT_RESULTS_PATH}")


if __name__ == "__main__":
    main()
