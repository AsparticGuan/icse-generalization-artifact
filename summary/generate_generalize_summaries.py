#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI


DEFAULT_DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset-patch"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results" / "generalize-summaries"

TOKEN = os.environ.get("LAB_LLM_TOKEN")
BASE_URL = os.environ.get("LAB_LLM_URL")
MODEL_NAME = os.environ.get("LAB_LLM_MODEL", "qwen3.5-plus")
TEMPERATURE = float(os.environ.get("LAB_LLM_TEMP", "0.3"))

SYSTEM_PROMPT = (
    "You are an expert LLVM InstCombine engineer. "
    "Given one representative test and the final source patch, "
    "explain how the issue was fixed by generalizing the test pattern."
)

USER_PROMPT_TEMPLATE = """\
You will receive:
1) A test case that demonstrates a missed optimization issue.
2) The source patch that fixes the issue.


Please summarize how developers generalized the test pattern into a broader optimization rule implemented by the patch.
Focus on:
- the core structural IR pattern,
- what dimensions were generalized (commutativity, constants, vectors, predicates, one-use constraints, safety guards, etc.),
- why the rewrite is correct/profitable.

Return ONLY JSON with this exact schema:
{{
  "summary": "string"
}}

Test case:
{first_test_body}

Patch:
{source_patch}
"""


def get_client() -> OpenAI:
    if TOKEN:
        if BASE_URL:
            return OpenAI(api_key=TOKEN, base_url=BASE_URL)
        return OpenAI(api_key=TOKEN)
    return OpenAI()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate generalization summaries from dataset-patch using "
            "source_patch + first test case."
        )
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=DEFAULT_DATASET_DIR,
        help="Directory containing dataset-patch JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write *_summary.json files.",
    )
    parser.add_argument(
        "--issues",
        type=str,
        default="",
        help="Comma-separated issue IDs (without .json). Empty means all issues.",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite existing summary files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call LLM, write placeholder summary for pipeline checks.",
    )
    return parser.parse_args()


def extract_first_test(data: dict[str, Any]) -> tuple[str, str, int]:
    test_files = data.get("tests", [])
    if not isinstance(test_files, list) or not test_files:
        raise ValueError("missing tests array")

    first_test_file = test_files[0]
    if not isinstance(first_test_file, dict):
        raise ValueError("tests[0] is not an object")

    tests = first_test_file.get("tests", [])
    if not isinstance(tests, list) or not tests:
        raise ValueError("tests[0].tests is empty")

    first_test = tests[0]
    if not isinstance(first_test, dict):
        raise ValueError("tests[0].tests[0] is not an object")

    test_name = str(first_test.get("test_name", "")).strip()
    test_body = str(first_test.get("test_body", "")).strip()
    if not test_body:
        raise ValueError("first test body is empty")

    return test_name, test_body, len(tests)


def call_model(client: OpenAI, prompt: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=TEMPERATURE,
        timeout=300,
    )
    return (resp.choices[0].message.content or "").strip()


def extract_summary_from_response(text: str) -> str:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            summary = obj.get("summary", "")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
    except Exception:
        pass
    raise ValueError("LLM output is not valid JSON with non-empty summary")


def process_issue(
    issue_id: str,
    dataset_dir: Path,
    output_dir: Path,
    client: OpenAI | None,
    force: bool,
    dry_run: bool,
) -> None:
    src_path = dataset_dir / f"{issue_id}.json"
    out_path = output_dir / f"{issue_id}_summary.json"

    if not src_path.is_file():
        print(f"[ERROR] Missing dataset file: {src_path}")
        return
    if out_path.exists() and not force:
        print(f"[SKIP] Exists: {out_path} (use -f to overwrite)")
        return

    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    pr_number = data.get("pr_number")
    title = str(data.get("title", "")).strip()
    source_patch = str(data.get("source_patch", "")).strip()
    if not source_patch:
        print(f"[ERROR] {issue_id}: source_patch is empty")
        return

    try:
        before_test_name, first_test_body, num_after_cases = extract_first_test(data)
    except Exception as e:
        print(f"[ERROR] {issue_id}: {e}")
        return

    if dry_run:
        summary = "[dry-run] summary generation skipped."
    else:
        if client is None:
            raise RuntimeError("LLM client is required when not in dry-run mode")
        prompt = USER_PROMPT_TEMPLATE.format(
            pr_number=pr_number,
            title=title,
            before_test_name=before_test_name,
            num_after_cases=num_after_cases,
            first_test_body=first_test_body,
            source_patch=source_patch,
        )
        raw = call_model(client, prompt)
        summary = extract_summary_from_response(raw)

    output = {
        "pr_number": pr_number,
        "title": title,
        "before_test_name": before_test_name,
        "num_after_cases": num_after_cases,
        "summary": summary,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[OK] Wrote {out_path}")


def build_issue_list(dataset_dir: Path, issues_arg: str) -> list[str]:
    if issues_arg.strip():
        return [x.strip() for x in issues_arg.split(",") if x.strip()]
    return sorted(p.stem for p in dataset_dir.glob("*.json"))


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not dataset_dir.is_dir():
        raise SystemExit(f"[ERROR] dataset-dir does not exist: {dataset_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    issue_ids = build_issue_list(dataset_dir, args.issues)
    if not issue_ids:
        raise SystemExit("[ERROR] No issue IDs found to process")

    client = None if args.dry_run else get_client()
    if not args.dry_run and not TOKEN and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("[ERROR] Set LAB_LLM_TOKEN or OPENAI_API_KEY before running")

    print(f"[INFO] dataset-dir: {dataset_dir}")
    print(f"[INFO] output-dir:  {output_dir}")
    print(f"[INFO] model:       {MODEL_NAME}")
    print(f"[INFO] issues:      {len(issue_ids)}")

    for issue_id in issue_ids:
        process_issue(
            issue_id=issue_id,
            dataset_dir=dataset_dir,
            output_dir=output_dir,
            client=client,
            force=args.force,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
