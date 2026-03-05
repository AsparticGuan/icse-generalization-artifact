#!/usr/bin/env python3
import os
import re
import json
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from openai import OpenAI

sys.path.append(os.path.join(os.path.dirname(os.environ.get("LAB_DATASET_DIR", ".")), "scripts"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "scripts"))
import llvm_helper  # pyright: ignore[reportMissingImports]  # noqa: E402
from lab_env import Environment as Env  # pyright: ignore[reportMissingImports]  # noqa: E402

DATASET_DIR = os.environ.get("LAB_DATASET_DIR", "dataset")
OUTPUT_RESULTS_DIR = os.environ.get(
    "LAB_GENERALIZE_DIR",
    os.path.join(os.path.dirname(__file__), "..", "results", "generalize"),
)

OVERALL_SUMMARY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "results", "generalize-summaries", "overall_summary.md"
)


def _load_overall_summary() -> str:
    """Load overall summary content from file; return empty string if file missing."""
    try:
        with open(OVERALL_SUMMARY_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

OVERALL_SUMMARY = _load_overall_summary()

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
2) Generalize it into a reusable pattern and summarize the pattern so it could be implemented as an InstCombine fold.
3) Generate many additional LLVM IR test cases that are semantically equivalent and should trigger the same transformation, including corner cases.

## Reference: common missed elements and patterns

When generalizing, consider the following summary of elements frequently missed in InstCombine and patterns that are often not well handled. 

{overall_summary}

## Output format requirement (strict)

Return **ONLY** JSON (a single JSON object). No Markdown, no extra text.

## What I will provide

- The original LLVM IR snippet (minimal reproducer).
- The target triple/datalayout if relevant. 

## What you must output (in JSON)

Include the following top-level keys (you may add more keys if useful):

- "pattern" (generalized pattern description)
- "testcases" (an array of testcase objects; include only positive cases)

Each testcase object must include at least:

- "name"
- "ir" (the input IR for this case)
- "expected_optimized_ir" (what InstCombine should produce, at least the key simplified instructions; full function preferred)

---

## Here is my single example 

{source_code}
{expected_optmized_code}
""".strip()


def extract_json_obj(text: str) -> dict | None:
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


def run_opt_instcombine(ir: str, timeout_s: int = 10) -> tuple[bool, str]:
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


def run_cost_model(ir: str) -> tuple[bool, int, str]:
    if not isinstance(ir, str) or not ir.strip():
        return False, -1, "empty ir"
    try:
        with tempfile.NamedTemporaryFile(suffix=".ll") as code_file:
            code_file.write(ir.encode())
            code_file.flush()
            out = subprocess.check_output(
                [llvm_helper.llvm_cost_tool, code_file.name],
                stderr=subprocess.STDOUT,
            ).decode()
            m = re.search(r"Cost:\s*(\d+)", out)
            if not m:
                return False, -1, out
            return True, int(m.group(1)), out
    except Exception as e:
        err = str(e)
        if hasattr(e, "output"):
            err += "\n" + llvm_helper.decode_output(e.output)
        return False, -1, err


def alive2_check(src: str, tgt: str) -> tuple[bool, Any]:
    if "ptr" in src and "target datalayout" not in src:
        src = 'target datalayout = "p:8:8:8"\n' + src
    if "ptr" in tgt and "target datalayout" not in tgt:
        tgt = 'target datalayout = "p:8:8:8"\n' + tgt
    return llvm_helper.alive2_check(src, tgt, "-src-unroll=8 -tgt-unroll=8")


def find_first_testcase(data: dict) -> tuple[dict, dict] | None:
    tests = data.get("tests", [])
    has_current = False
    for test_file in tests:
        for test in test_file.get("tests", []):
            if isinstance(test, dict) and "current_optimized_program" in test:
                has_current = True
                break
        if has_current:
            break

    if has_current:
        for test_file in tests:
            for test in test_file.get("tests", []):
                if not isinstance(test, dict):
                    continue
                if not test.get("source_program") or not test.get("expect_optimized_program"):
                    continue
                if "current_optimized_program" not in test:
                    continue
                current = test.get("current_optimized_program")
                expected = test.get("expect_optimized_program")
                if isinstance(current, str) and isinstance(expected, str) and current != expected:
                    return test_file, test
        return None

    for test_file in tests:
        for test in test_file.get("tests", []):
            if test.get("source_program") and test.get("expect_optimized_program"):
                return test_file, test
    return None


def call_model(prompt: str) -> str:
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


def write_result(path: str, result: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Failed to write result to {path}: {e}")


override = False


def process_issue(issue_id: str) -> None:
    """Process a single issue: call model, validate testcases, write result."""
    dataset_file = f"{issue_id}.json"
    json_path = os.path.join(DATASET_DIR, dataset_file)
    out_path = os.path.join(OUTPUT_RESULTS_DIR, f"{issue_id}.json")

    # --- skip check ---
    if not override and os.path.exists(out_path):
        print(f"Skip {issue_id} (already at {out_path}, use -f to override)")
        return

    if not os.path.isfile(json_path):
        print(f"[ERROR] Dataset file not found: {json_path}")
        return

    print(f"\n[INFO] Processing issue {issue_id}...")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        err = f"Failed to read/parse {json_path}: {e}"
        print(f"[ERROR] {err}")
        write_result(out_path, {"issue_id": issue_id, "dataset_file": dataset_file, "error": err})
        return

    found = find_first_testcase(data)
    if not found:
        err = "no valid testcase found"
        print(f"[SKIP] No valid testcase found for {issue_id}")
        write_result(out_path, {"issue_id": issue_id, "dataset_file": dataset_file, "error": err})
        return

    _test_file, test = found
    source_program = test.get("source_program", "")
    expect_optimized_program = test.get("expect_optimized_program", "")
    if not source_program or not expect_optimized_program:
        err = "source_program or expect_optimized_program missing"
        print(f"[ERROR] {err} for {issue_id}")
        write_result(out_path, {"issue_id": issue_id, "dataset_file": dataset_file, "error": err})
        return

    prompt = PROMPT_TEMPLATE.format(
        overall_summary=OVERALL_SUMMARY,
        source_code=source_program,
        expected_optmized_code=expect_optimized_program,
    )

    print(f"[INFO] Calling model for {issue_id}...")
    try:
        model_output = call_model(prompt)
    except Exception as e:
        err = f"model call failed: {e}"
        print(f"[ERROR] {err}")
        write_result(out_path, {
            "issue_id": issue_id, "dataset_file": dataset_file,
            "prompt": prompt, "error": err,
        })
        return

    parsed = extract_json_obj(model_output)

    results_entry: dict[str, Any] = {
        "issue_id": issue_id,
        "dataset_file": dataset_file,
        "prompt": prompt,
        "model_output": model_output,
        "parsed": parsed,
        "validation": [],
    }

    if parsed is None:
        print("[ERROR] Failed to parse model output as JSON")
        results_entry["error"] = "failed to parse model output as JSON"
        write_result(out_path, results_entry)
        return

    print(f"[INFO] Building LLVM for {issue_id}...")
    try:
        env = Env(issue_id, BASEMODEL_CUTOFF, max_build_jobs=MAX_BUILD_JOBS)
        env.reset()
        build_ok, build_log = env.build()
        results_entry["build"] = {"ok": build_ok, "log": build_log}
        if not build_ok:
            print("[ERROR] Build failed; aborting validation")
            write_result(out_path, results_entry)
            return
    except Exception as e:
        results_entry["build"] = {"ok": False, "log": str(e)}
        print(f"[ERROR] Build failed: {e}")
        write_result(out_path, results_entry)
        return

    testcases = parsed.get("testcases", [])
    if not isinstance(testcases, list):
        testcases = []

    for idx, tc in enumerate(testcases):
        if not isinstance(tc, dict):
            results_entry["validation"].append(
                {"index": idx, "ok": False, "error": "testcase is not an object"}
            )
            continue

        name = tc.get("name", f"case_{idx}")
        src_ir = tc.get("ir", "")
        exp_ir = tc.get("expected_optimized_ir", "")
        if (
            not isinstance(src_ir, str)
            or not isinstance(exp_ir, str)
            or not src_ir.strip()
        ):
            results_entry["validation"].append(
                {"name": name, "ok": False, "error": "missing ir or expected_optimized_ir"}
            )
            continue

        opt_ok, opt_out = run_opt_instcombine(src_ir)
        if not opt_ok:
            results_entry["validation"].append(
                {"name": name, "ok": False, "error": opt_out}
            )
            continue

        cost_src_ok, cost_src, cost_src_log = run_cost_model(src_ir)
        cost_exp_ok, cost_exp, cost_exp_log = run_cost_model(exp_ir)
        cost_opt_ok, cost_opt, cost_opt_log = run_cost_model(opt_out)
        cost_ok = cost_src_ok and cost_exp_ok and cost_opt_ok
        expected_le_opt = cost_ok and cost_exp <= cost_opt
        expected_le_src = cost_ok and cost_exp <= cost_src

        a2_src_exp_ok = None
        a2_src_exp_log = None
        if expected_le_opt and expected_le_src:
            a2_src_exp_ok, a2_src_exp_log = alive2_check(src_ir, exp_ir)

        a2_opt_exp_ok = None
        a2_opt_exp_log = None
        a2_exp_opt_ok = None
        a2_exp_opt_log = None
        equivalent = None
        if expected_le_opt and expected_le_src and a2_src_exp_ok:
            a2_opt_exp_ok, a2_opt_exp_log = alive2_check(opt_out, exp_ir)
            a2_exp_opt_ok, a2_exp_opt_log = alive2_check(exp_ir, opt_out)
            equivalent = a2_opt_exp_ok and a2_exp_opt_ok

        missed_optimization = bool(
            expected_le_opt and expected_le_src and a2_src_exp_ok and equivalent is True
        )

        results_entry["validation"].append(
            {
                "name": name,
                "missed_optimization": missed_optimization,
                "cost": {
                    "source": cost_src,
                    "expected": cost_exp,
                    "current_opt": cost_opt,
                    "expected_le_current_opt": expected_le_opt,
                    "expected_le_source": expected_le_src,
                    "ok": cost_ok,
                    "source_log": cost_src_log,
                    "expected_log": cost_exp_log,
                    "current_opt_log": cost_opt_log,
                },
                "source_to_expected_ok": a2_src_exp_ok,
                "opt_equivalent_expected": equivalent,
                "alive2": {
                    "src_to_expected": a2_src_exp_log,
                    "opt_to_expected": a2_opt_exp_log,
                    "expected_to_opt": a2_exp_opt_log,
                },
            }
        )

    write_result(out_path, results_entry)


os.makedirs(OUTPUT_RESULTS_DIR, exist_ok=True)

if len(sys.argv) == 1:
    # No args: process every dataset file
    task_list = sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(DATASET_DIR)
        if os.path.isfile(os.path.join(DATASET_DIR, f)) and f.endswith(".json")
    )
else:
    task_list = [x.strip() for x in sys.argv[1].split(",") if x.strip()]
    if len(sys.argv) >= 3 and sys.argv[2] == "-f":
        override = True

for task in task_list:
    process_issue(task)
