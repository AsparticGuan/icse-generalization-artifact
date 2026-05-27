#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def resolve_issue_id(data: dict[str, Any], json_path: Path) -> str:
    issue_id = data.get("test_issue_id") or data.get("patch_issue_id")
    if issue_id:
        return str(issue_id)

    # Fallback to directory convention: <model>/<issue>/<timestamp>/accepted_tests.json
    try:
        return json_path.parent.parent.name
    except IndexError:
        return json_path.stem


def has_non_empty_tests(data: dict[str, Any]) -> bool:
    tests = data.get("tests")
    if isinstance(tests, list):
        return len(tests) > 0
    return bool(tests)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Count issues whose tests field is non-empty for a specific model and timestamp."
        )
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Model name under results/discriminating_tests, e.g. qwen3.5-plus",
    )
    parser.add_argument(
        "--timestamp",
        required=True,
        help="Timestamp directory name, e.g. 20260408-001336",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "discriminating_tests_llmgen",
        help="Base path of discriminating_tests (default: results/discriminating_tests).",
    )
    args = parser.parse_args()

    base_dir = args.base_dir.resolve()
    model_dir = base_dir / args.model

    if not base_dir.exists():
        raise SystemExit(f"Base directory does not exist: {base_dir}")
    if not model_dir.exists():
        raise SystemExit(f"Model directory does not exist: {model_dir}")

    pattern = f"*/{args.timestamp}/accepted_tests.json"
    json_files = sorted(model_dir.glob(pattern))
    issue_non_empty: dict[str, bool] = {}

    for json_file in json_files:
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Skip invalid file: {json_file} ({exc})")
            continue

        issue_id = resolve_issue_id(data, json_file)
        non_empty = has_non_empty_tests(data)
        issue_non_empty[issue_id] = issue_non_empty.get(issue_id, False) or non_empty

    total_issues = len(issue_non_empty)
    non_empty_issues = sum(1 for v in issue_non_empty.values() if v)
    ratio = (non_empty_issues / total_issues * 100.0) if total_issues else 0.0
    non_empty_issue_ids = sorted(
        (issue_id for issue_id, is_non_empty in issue_non_empty.items() if is_non_empty),
        key=lambda x: int(x) if x.isdigit() else x,
    )

    print(f"Base dir: {base_dir}")
    print(f"Model: {args.model}")
    print(f"Timestamp: {args.timestamp}")
    print(f"accepted_tests.json files scanned: {len(json_files)}")
    print(f"Total issues: {total_issues}")
    print(f"Issues with non-empty tests: {non_empty_issues}")
    print(f"Ratio: {ratio:.2f}%")
    print("Non-empty test issues:")
    if non_empty_issue_ids:
        print("\n".join(non_empty_issue_ids))
    else:
        print("(none)")


if __name__ == "__main__":
    main()
