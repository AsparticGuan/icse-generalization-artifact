#!/usr/bin/env python3
"""
利用 LLM 根据 results/generalize-summaries 下所有 summary 一次性生成整体总结。
仿照「bug-triggering strategies + 共同要素/模式」的 prompt 风格，输出为
Elements Frequently Missed + High-Level Patterns Not Well Handled。

用法:
  export LAB_LLM_TOKEN=your_token
  export LAB_LLM_URL=https://...   # 可选
  export LAB_LLM_MODEL=...         # 可选
  python summary/summarize_all_summaries.py [--summaries-dir DIR] [--output PATH] [--max-summaries N] [--dry-run]

注意：若 summary 条数很多（如 800+），单次 prompt 会超长，可先用 --max-summaries 限制条数做整体总结，或后续再考虑分段。
"""
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_ENV_PATH = PROJECT_ROOT / "agent" / ".env"
ROOT_ENV_PATH = PROJECT_ROOT / ".env"

try:
    from dotenv import load_dotenv

    load_dotenv(AGENT_ENV_PATH, override=False)
    load_dotenv(ROOT_ENV_PATH, override=False)
except ImportError:
    pass

# 与 pipeline/generalize.py 保持一致
TOKEN = os.environ.get("LAB_LLM_TOKEN")
BASE_URL = os.environ.get("LAB_LLM_URL")
MODEL_NAME = os.environ.get("LAB_LLM_MODEL", "google/gemini-3.1-pro-preview")
TEMPERATURE = float(os.environ.get("LAB_LLM_TEMP", "0.3"))
DEFAULT_SUMMARIES_DIR = PROJECT_ROOT / "results" / "generalize-summaries"

PROMPT_SYSTEM_SUMMARY = """\
You are an expert in LLVM IR and compiler optimizations, especially in {component}.

## Context ##
Users provide a list of bug-triggering strategies at the LLVM IR level, attached with examples of original and optimized LLVM IR code that demonstrate the bug in {component}.
These strategies are in the same optimization pass, and they might share similar issues, such as elements frequently missed and high-level patterns that are not well handled.

## Your Task ##
Your task is to analyze the provided bug-triggering strategies and their examples, and summarize the common elements and patterns.

## You Will Receive ##
- A list of bug-triggering strategies, each with a description and examples of original and optimized LLVM IR code that demonstrate the bug.
"""

PROMPT_SUMMARY = """\
Here is a list of bug-triggering strategies at the LLVM IR level, each with a description and examples of original and optimized LLVM IR code that demonstrate the bug in {component}:

{strategies}

## Your Task ##
You should construct a subsystem knowledge base for the optimization pass in {component} based on the provided bug-triggering strategies and their examples. \
Follow the steps below to analyze the strategies and summarize the common elements and patterns:

1. **Understanding the Issues**: Read through all the provided bug-triggering strategies and their examples to fully understand the issues they demonstrate. \
Pay special attention to the LLVM IR code examples, as they can provide insights into the specific instruction patterns and transformations that lead to the bugs.
2. **Categorizing the Issues**: Try to categorize the issues based on common elements or patterns. For example, you might find that certain instruction types, operand patterns, \
or transformation sequences are frequently involved in the bugs.
3. **Identifying Frequently Missed Elements**: Identify specific elements (e.g., instruction types, operand patterns) that are frequently missed in the optimization pass, which can lead to bugs.
4. **Identifying High-Level Patterns**: Identify high-level patterns (e.g., specific combinations of instructions or transformations) that are not well handled in the optimization pass, which can lead to bugs.

## Output Format ##
The output should be in Markdown format and follow this structure:

- **Elements Frequently Missed**: A list of specific elements that are frequently missed in the optimization pass, along with a brief explanation of why they are missed. \
It should start with ## Elements Frequently Missed and be followed by a bullet point list of the elements.
- **High-Level Patterns Not Well Handled**: A description of high-level patterns that are not well handled in the optimization pass, along with an explanation of the issues they cause \
and why they are not well handled. It should start with ## Patterns Not Well Handled and be followed by a detailed description of the patterns titled with ### Pattern 1, ### Pattern 2, etc.

No other explanation is needed. The output should only have two sections: Elements Frequently Missed and High-Level Patterns Not Well Handled, as described above.
"""


def get_client() -> OpenAI:
    if TOKEN:
        if BASE_URL:
            return OpenAI(api_key=TOKEN, base_url=BASE_URL)
        return OpenAI(api_key=TOKEN)
    return OpenAI()


def load_summaries(dir_path: Path) -> list[dict]:
    """加载所有 *_summary.json，返回 [{pr_number, title, summary, ...}, ...]"""
    out = []
    for f in sorted(dir_path.glob("*_summary.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict) and data.get("summary"):
                out.append({
                    "pr_number": data.get("pr_number"),
                    "title": data.get("title", ""),
                    "before_test_name": data.get("before_test_name", ""),
                    "num_after_cases": data.get("num_after_cases"),
                    "summary": data["summary"].strip(),
                })
        except Exception as e:
            print(f"[WARN] Skip {f.name}: {e}")
    return out


def format_strategies(summaries: list[dict]) -> str:
    """将所有 summary 格式化为 prompt 中的 strategies 文本。"""
    lines = []
    for i, it in enumerate(summaries, 1):
        lines.append(f"--- Strategy {i} (PR #{it.get('pr_number', '?')}) ---")
        lines.append(f"Title: {it.get('title', '')}")
        lines.append("Description / examples:")
        lines.append(it.get("summary", ""))
        lines.append("")
    return "\n".join(lines).strip()


def call_model(
    client: OpenAI,
    model_name: str,
    system: str,
    user: str,
    timeout: int = 600,
) -> str:
    resp = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=TEMPERATURE,
        timeout=timeout,
    )
    return (resp.choices[0].message.content or "").strip()


def run_direct_summary(
    summaries: list[dict],
    component: str,
    model_name: str,
    client: OpenAI,
    dry_run: bool = False,
) -> dict:
    """
    一次性把所有 summary 交给 LLM，按给定 prompt 生成整体总结。
    返回 {"overall_summary": "...", "meta": {...}}
    """
    strategies_text = format_strategies(summaries)
    n_chars = len(strategies_text)
    if n_chars > 100_000:
        print(f"[WARN] Strategies text is very long ({n_chars} chars). Consider --max-summaries to avoid context limit.")
    system = PROMPT_SYSTEM_SUMMARY.format(component=component)
    user = PROMPT_SUMMARY.format(component=component, strategies=strategies_text)

    if dry_run:
        overall = "[dry-run] LLM not called."
        print(f"[dry-run] Prompt length: system ~{len(system)}, user ~{len(user)} chars.")
    else:
        print("Calling LLM (single request with all summaries)...")
        overall = call_model(client, model_name, system, user, timeout=600)
        print("Done.")

    return {
        "meta": {
            "num_summaries": len(summaries),
            "component": component,
            "model": model_name,
        },
        "overall_summary": overall,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="用 LLM 根据所有 generalize-summaries 一次性生成整体总结（Elements Frequently Missed + Patterns Not Well Handled）"
    )
    parser.add_argument(
        "--summaries-dir",
        type=Path,
        default=DEFAULT_SUMMARIES_DIR,
        help="存放 *_summary.json 的目录",
    )
    parser.add_argument(
        "--component",
        type=str,
        default="InstCombine",
        help="优化 pass 名称，用于 prompt 中的 {component}",
    )
    parser.add_argument(
        "--max-summaries",
        type=int,
        default=None,
        help="最多随机使用 N 条 summary（默认全部）。若总字符数过大可设此值避免超上下文",
    )
    parser.add_argument(
        "--sample-seed",
        type=int,
        default=None,
        help="随机抽样种子。设置后可复现同一批被抽中的 summary",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出路径（默认：summaries-dir/overall_summary.json）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只加载并构造 prompt，不调用 LLM",
    )
    parser.add_argument(
        "--md",
        action="store_true",
        help="额外写一份仅含整体总结的 Markdown 文件（overall_summary.md）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=MODEL_NAME,
        help=f'LLM model name (default from LAB_LLM_MODEL or "{MODEL_NAME}")',
    )
    args = parser.parse_args()
    model_name = args.model.strip()
    if not model_name:
        print("[ERROR] --model cannot be empty")
        raise SystemExit(1)

    if not args.summaries_dir.is_dir():
        print(f"[ERROR] Not a directory: {args.summaries_dir}")
        raise SystemExit(1)

    summaries = load_summaries(args.summaries_dir)
    if args.max_summaries is not None and len(summaries) > args.max_summaries:
        if args.max_summaries <= 0:
            print("[ERROR] --max-summaries must be >= 1 when provided")
            raise SystemExit(1)
        rng = random.Random(args.sample_seed)
        summaries = rng.sample(summaries, args.max_summaries)
        if args.sample_seed is None:
            print(f"Using random {len(summaries)} summaries (--max-summaries={args.max_summaries})")
        else:
            print(
                f"Using random {len(summaries)} summaries (--max-summaries={args.max_summaries}, --sample-seed={args.sample_seed})"
            )
    else:
        print(f"Loaded {len(summaries)} summaries from {args.summaries_dir}")

    if not summaries:
        print("[ERROR] No summaries found.")
        raise SystemExit(1)

    if args.output is not None:
        output_path = args.output
    else:
        if args.max_summaries is not None:
            output_path = args.summaries_dir / f"overall_summary-max{args.max_summaries}.json"
        else:
            output_path = args.summaries_dir / "overall_summary.json"
    output_path = output_path.resolve()

    if args.dry_run:
        result = run_direct_summary(
            summaries, args.component, model_name, get_client(), dry_run=True
        )
    else:
        if not TOKEN and not os.environ.get("OPENAI_API_KEY"):
            print("[ERROR] Set LAB_LLM_TOKEN or OPENAI_API_KEY for LLM calls.")
            raise SystemExit(1)
        client = get_client()
        result = run_direct_summary(
            summaries, args.component, model_name, client, dry_run=False
        )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if args.md and result.get("overall_summary") and not args.dry_run:
        md_path = output_path.with_suffix(".md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(result["overall_summary"])
        print(f"Also written to {md_path}")

    print(f"Written to {output_path}")
    if result.get("overall_summary") and not args.dry_run:
        print("\n--- Overall summary (first 2000 chars) ---")
        print(result["overall_summary"][:2000])
        if len(result["overall_summary"]) > 2000:
            print("...")


if __name__ == "__main__":
    main()
