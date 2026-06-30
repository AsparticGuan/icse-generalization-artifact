#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import OpenAI
from tqdm import tqdm


DEFAULT_DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset-patch"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results" / "generalize-summaries"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_ENV_PATH = PROJECT_ROOT / "agent" / ".env"
ROOT_ENV_PATH = PROJECT_ROOT / ".env"

try:
    from dotenv import load_dotenv

    load_dotenv(AGENT_ENV_PATH, override=False)
    load_dotenv(ROOT_ENV_PATH, override=False)
except ImportError:
    pass

TOKEN = os.environ.get("LAB_LLM_TOKEN")
BASE_URL = os.environ.get("LAB_LLM_URL", "https://api.openai.com/v1")
DEFAULT_MODEL_NAME = os.environ.get("LAB_LLM_MODEL", "qwen3.5-plus")
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
    kwargs: dict[str, object] = {"base_url": BASE_URL}
    if TOKEN:
        kwargs["api_key"] = TOKEN
    return OpenAI(**kwargs)


def _is_official_openai_endpoint(api_url: str) -> bool:
    url = (api_url or "").strip()
    if not url:
        return True
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        host = ""
    if not host:
        return False
    return host == "api.openai.com" or host.endswith(".openai.com")


def _api_model_name(raw: str, api_url: str) -> str:
    model = raw.strip()
    if not model:
        return model
    if _is_official_openai_endpoint(api_url) and "/" in model:
        return model.split("/", 1)[1]
    if model.startswith("custom_openai/"):
        return model.split("/", 1)[1]
    return model


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
        help="Base directory. Summaries are written to <output-dir>/<model>/.",
    )
    parser.add_argument(
        "--issues",
        type=str,
        default="",
        help="Comma-separated issue IDs (without .json). Empty means all issues.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL_NAME,
        help=(
            "Model name for chat.completions; overrides LAB_LLM_MODEL when provided. "
            f'Default: "{DEFAULT_MODEL_NAME}".'
        ),
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
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("LAB_SUMMARY_WORKERS", "8")),
        help="Number of parallel workers for issue processing (default: 8).",
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


def call_model(client: OpenAI, model_name: str, prompt: str) -> str:
    api_model = _api_model_name(model_name, BASE_URL)
    request_kwargs: dict[str, Any] = {
        "model": api_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": TEMPERATURE,
        "timeout": 300,
    }
    # Some providers honor OpenAI's JSON mode; if unsupported we fall back.
    try:
        resp = client.chat.completions.create(
            **request_kwargs,
            response_format={"type": "json_object"},
        )
    except Exception:
        resp = client.chat.completions.create(**request_kwargs)
    return (resp.choices[0].message.content or "").strip()


def _extract_json_candidates(text: str) -> list[str]:
    stripped = (text or "").strip()
    if not stripped:
        return []

    candidates = [stripped]

    # Common pattern: ```json ... ```
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, flags=re.IGNORECASE)
    for block in fenced:
        block_text = block.strip()
        if block_text:
            candidates.append(block_text)

    # Try to find JSON objects embedded in surrounding prose.
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            obj, end_idx = decoder.raw_decode(stripped[idx:])
            if isinstance(obj, dict):
                candidates.append(stripped[idx : idx + end_idx].strip())
        except Exception:
            continue

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def extract_summary_from_response(text: str) -> str:
    for candidate in _extract_json_candidates(text):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                summary = obj.get("summary", "")
                if isinstance(summary, str) and summary.strip():
                    return summary.strip()
        except Exception:
            continue

    # Last-chance fallback: accept `"summary": "..."` from non-standard wrappers.
    try:
        match = re.search(r'"summary"\s*:\s*"((?:\\.|[^"\\])*)"', text or "", flags=re.DOTALL)
        if match:
            summary = json.loads(f'"{match.group(1)}"')
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
    except Exception:
        pass
    raise ValueError("LLM output is not valid JSON with non-empty summary")


def process_issue(
    issue_id: str,
    dataset_dir: Path,
    output_dir: Path,
    model_name: str,
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
        client = get_client()
        prompt = USER_PROMPT_TEMPLATE.format(
            pr_number=pr_number,
            title=title,
            before_test_name=before_test_name,
            num_after_cases=num_after_cases,
            first_test_body=first_test_body,
            source_patch=source_patch,
        )
        raw = call_model(client, model_name, prompt)
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


def sanitize_model_dir_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in name.strip())
    return cleaned or "unknown-model"


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()
    output_base_dir = args.output_dir.resolve()
    model_name = args.model.strip()
    workers = args.workers
    if not model_name:
        raise SystemExit("[ERROR] --model cannot be empty")
    if workers <= 0:
        raise SystemExit("[ERROR] --workers must be >= 1")

    if not dataset_dir.is_dir():
        raise SystemExit(f"[ERROR] dataset-dir does not exist: {dataset_dir}")

    model_dir_name = sanitize_model_dir_name(model_name)
    output_dir = output_base_dir / model_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    issue_ids = build_issue_list(dataset_dir, args.issues)
    if not issue_ids:
        raise SystemExit("[ERROR] No issue IDs found to process")

    print(f"[INFO] dataset-dir: {dataset_dir}")
    print(f"[INFO] output-base: {output_base_dir}")
    print(f"[INFO] output-dir:  {output_dir}")
    print(f"[INFO] model:       {model_name}")
    print(f"[INFO] issues:      {len(issue_ids)}")
    print(f"[INFO] workers:     {workers}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                process_issue,
                issue_id=issue_id,
                dataset_dir=dataset_dir,
                output_dir=output_dir,
                model_name=model_name,
                force=args.force,
                dry_run=args.dry_run,
            )
            for issue_id in issue_ids
        ]
        with tqdm(total=len(futures), desc="Generating summaries", unit="issue") as pbar:
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"[ERROR] Worker failed: {e}")
                finally:
                    pbar.update(1)


if __name__ == "__main__":
    main()
