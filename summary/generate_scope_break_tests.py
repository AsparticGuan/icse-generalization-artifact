#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from openai import OpenAI


_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_PROJECT_DATASET_PATCH_DIR = _PROJECT_ROOT / "dataset-patch"
_DEFAULT_SUMMARIES_DIR = _PROJECT_ROOT / "results" / "generalize-summaries"
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "results" / "generalize-scope-break"

# 本流程只依赖 dataset-patch：summary 与 golden patch 数据都来自这里。
os.environ["LAB_DATASET_DIR"] = str(_PROJECT_DATASET_PATCH_DIR)
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(_PROJECT_ROOT / "agent"))

from env_config import cfg  # noqa: E402

cfg.dataset_dir = str(_PROJECT_DATASET_PATCH_DIR)
os.environ.setdefault("LAB_LLVM_DIR", cfg.llvm_dir)
os.environ.setdefault("LAB_LLVM_BUILD_DIR", cfg.llvm_build_dir)
os.environ["LAB_DATASET_DIR"] = cfg.dataset_dir
os.environ.setdefault("LAB_LLVM_ALIVE_TV", cfg.alive_tv)
os.environ.setdefault("LAB_LLVM_COST_TOOL", cfg.cost_tool)

import llvm_helper  # noqa: E402
import retest_patches as rp  # noqa: E402

TOKEN = os.environ.get("LAB_LLM_TOKEN")
BASE_URL = os.environ.get("LAB_LLM_URL")
MODEL_NAME = os.environ.get("LAB_LLM_MODEL", "qwen3.5-plus")
TEMPERATURE = float(os.environ.get("LAB_LLM_TEMP", "0.4"))

GEN_SYSTEM_PROMPT = (
    "You are an expert LLVM InstCombine engineer. "
    "Generate LLVM IR tests that are likely outside a provided summary scope."
)

GEN_USER_PROMPT_TEMPLATE = """\
Issue: {issue_id}
PR #{pr_number}: {title}

Summary of known covered pattern:
{summary}

Golden source patch:
{source_patch}

Existing tests from dataset (examples already known):
{known_tests}

Task:
Generate {per_round} candidate tests that are likely OUTSIDE the summary scope but still optimizable by the golden patch.
Each candidate must include:
- test_name
- source_program (full LLVM IR function/module text)
- expect_optimized_program
- outside_scope_reason (why this case is outside the summary text)

Return ONLY JSON array:
[
  {{
    "test_name": "...",
    "source_program": "...",
    "expect_optimized_program": "...",
    "outside_scope_reason": "..."
  }}
]
"""

def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_path_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value)


def get_client() -> OpenAI:
    if TOKEN:
        if BASE_URL:
            return OpenAI(api_key=TOKEN, base_url=BASE_URL)
        return OpenAI(api_key=TOKEN)
    return OpenAI()


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    txt = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", txt, re.IGNORECASE)
    if m:
        txt = m.group(1).strip()
    try:
        obj = json.loads(txt)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, list):
        return []
    out: list[dict[str, Any]] = []
    for item in obj:
        if isinstance(item, dict):
            out.append(item)
    return out


def _call_model(client: OpenAI, *, model: str, system: str, user: str, timeout: float) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=TEMPERATURE,
        timeout=timeout,
    )
    return (resp.choices[0].message.content or "").strip()


def _strip_diff_only(patch: str) -> str:
    idx = patch.find("diff --git")
    if idx >= 0:
        return patch[idx:]
    return patch


def _strip_opt_module_prefix(text: str) -> str:
    return text.removeprefix(
        "; ModuleID = '<stdin>'\nsource_filename = \"<stdin>\"\n"
    ).removeprefix("\n")


def _run_opt(source_ll: str, pass_name: str) -> tuple[bool, str, str]:
    opt_bin = Path(llvm_helper.llvm_build_dir) / "bin" / "opt"
    if not opt_bin.is_file():
        return False, "", f"opt not found: {opt_bin}"
    try:
        proc = subprocess.run(
            [str(opt_bin), f"--passes={pass_name}", "-S"],
            input=source_ll.encode(),
            timeout=30.0,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return False, "", (proc.stderr or b"").decode(errors="replace")
        return True, proc.stdout.decode(errors="replace"), ""
    except Exception as e:
        return False, "", str(e)


def _eval_opt_side(
    source_program: str,
    expect_optimized_program: str,
    pass_name: str,
) -> dict[str, Any]:
    ok, output, err = _run_opt(source_program, pass_name)
    if not ok:
        return {"opt_ok": False, "opt_error": err, "alive2_ok": False, "cost_ok": False}

    new_input = llvm_helper.copy_triple(source_program, output.encode())
    new_input = llvm_helper.copy_datalayout(new_input, output.encode())
    alive_ok, alive_log = llvm_helper.alive2_check(new_input, output, additional_args=None)
    cur = _strip_opt_module_prefix(output)
    _unused_cost_ok, costs = llvm_helper.cost_check(
        source_program, expect_optimized_program, cur
    )
    src_cost = costs.get("source_program", -1)
    cur_cost = costs.get("current_optimized_program", -1)
    # This workflow only requires current optimization to strictly beat source.
    cost_ok = (
        isinstance(src_cost, int)
        and isinstance(cur_cost, int)
        and src_cost >= 0
        and cur_cost >= 0
        and cur_cost < src_cost
    )
    return {
        "opt_ok": True,
        "alive2_ok": bool(alive_ok),
        "cost_ok": bool(cost_ok),
        "alive2": alive_log,
        "costs": costs,
        "current_optimized_program": cur,
    }


def _validate_candidate(
    source_program: str,
    expect_optimized_program: str,
    pass_name: str,
) -> tuple[bool, dict[str, Any]]:
    log = _eval_opt_side(source_program, expect_optimized_program, pass_name)
    ok = bool(log.get("opt_ok") and log.get("alive2_ok") and log.get("cost_ok"))
    return ok, log


def _extract_passes_for_opt(run_line: str) -> str:
    m = re.search(r"-passes='([^']+)'", run_line)
    if m:
        return m.group(1).strip()
    m = re.search(r'-passes="([^"]+)"', run_line)
    if m:
        return m.group(1).strip()
    m = re.search(r"-passes=(\S+)", run_line)
    if m:
        return m.group(1).strip().strip("'\"")
    return "instcombine"


def _find_first_opt_run_line(data: dict[str, Any]) -> str | None:
    for group in data.get("tests", []):
        cmds = group.get("commands", [])
        if cmds and cmds[0] and isinstance(cmds[0][0], str) and "opt" in cmds[0][0]:
            return cmds[0][0]
    return None


def _default_pass_from_hints(data: dict[str, Any]) -> str:
    comps = (data.get("hints") or {}).get("components") or []
    if not comps:
        return "instcombine"
    c0 = str(comps[0])
    if "SimplifyCFG" in c0:
        return "simplifycfg"
    if "Constraint" in c0:
        return "constraint-elimination"
    return "instcombine"


def _passes_pipeline_from_dataset(data: dict[str, Any]) -> str:
    run = _find_first_opt_run_line(data)
    if run:
        return _extract_passes_for_opt(run)
    return _default_pass_from_hints(data)


def _collect_known_tests(data: dict[str, Any], limit: int = 8) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for group in data.get("tests", []):
        for test in group.get("tests", []):
            if not isinstance(test, dict):
                continue
            name = str(test.get("test_name", "")).strip()
            src = str(test.get("source_program", "")).strip()
            exp = str(test.get("expect_optimized_program", "")).strip()
            if not src:
                continue
            out.append({"name": name, "source_program": src, "expect_optimized_program": exp})
            if len(out) >= limit:
                return out
    return out


def _known_tests_to_text(tests: list[dict[str, str]]) -> str:
    if not tests:
        return "(none)"
    chunks = []
    for i, t in enumerate(tests, 1):
        chunks.append(
            f"[known #{i}] {t.get('name', '')}\n"
            f"source:\n{t.get('source_program', '')}\n"
            f"expect:\n{t.get('expect_optimized_program', '')}\n"
        )
    return "\n".join(chunks)


def _prepare_build(
    *,
    llvm_dir: str,
    build_dir: Path,
    base_commit: str,
    patch_text: str,
    max_jobs: int,
    additional_cmake: list[str],
) -> tuple[bool, str]:
    build_meta = build_dir / ".summary_scope_build_meta.json"
    patch_body = _strip_diff_only(patch_text)
    fingerprint = {
        "base_commit": base_commit,
        "patch_sha256": _sha256_text(patch_body),
        "additional_cmake": additional_cmake,
        "llvm_dir": llvm_dir,
    }

    opt_bin = build_dir / "bin" / "opt"
    if opt_bin.is_file() and build_meta.is_file():
        try:
            old = json.loads(build_meta.read_text(encoding="utf-8"))
            if old == fingerprint:
                return True, "reuse existing build"
        except Exception:
            pass

    llvm_helper.llvm_dir = llvm_dir
    llvm_helper.llvm_build_dir = str(build_dir)
    os.environ["LAB_LLVM_DIR"] = llvm_dir

    llvm_helper.reset(base_commit)
    ok, log = llvm_helper.apply(patch_body)
    if not ok:
        return False, f"apply failed: {log}"

    build_dir.mkdir(parents=True, exist_ok=True)
    res, blog = llvm_helper.build(max_jobs, additional_cmake)
    if not res:
        return False, f"build failed: {blog[-4000:]}"

    try:
        build_meta.write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return True, ""


def _resolve_base_commit_from_commit_sha(llvm_dir: str, commit_sha: str) -> tuple[bool, str]:
    sha = (commit_sha or "").strip()
    if not sha:
        return False, "empty commit_sha"

    def _try_resolve_parent() -> tuple[bool, str]:
        try:
            parent = subprocess.check_output(
                ["git", "-C", llvm_dir, "rev-parse", f"{sha}^1"],
                text=True,
                stderr=subprocess.STDOUT,
                timeout=20.0,
            ).strip()
            if not parent:
                return False, f"failed to resolve parent for {sha}"
            return True, parent
        except Exception as e:
            return False, str(e)

    ok, parent_or_err = _try_resolve_parent()
    if ok:
        return True, parent_or_err

    # 兜底：本地镜像可能过旧，按 sha 从官方仓库补拉一次再重试。
    try:
        subprocess.run(
            [
                "git",
                "-C",
                llvm_dir,
                "fetch",
                "--no-tags",
                "https://github.com/llvm/llvm-project.git",
                sha,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=90.0,
        )
    except Exception:
        pass

    ok2, parent_or_err2 = _try_resolve_parent()
    if ok2:
        return True, parent_or_err2

    return (
        False,
        f"resolve base commit failed for {sha}: first={parent_or_err}; retry={parent_or_err2}",
    )


def _build_issue_list(summaries_dir: Path, issues_arg: str) -> list[str]:
    if issues_arg.strip():
        return [x.strip() for x in issues_arg.split(",") if x.strip()]
    return sorted(
        p.name.removesuffix("_summary.json")
        for p in summaries_dir.glob("*_summary.json")
        if p.name != "overall_summary.json"
    )


def _cleanup_issue_dirs(issue_id: str, build_dir: Path) -> None:
    targets = [
        _PROJECT_ROOT / "utils" / f"llvm-{issue_id}",
        build_dir,
    ]
    for path in targets:
        try:
            if path.is_dir():
                shutil.rmtree(path)
                print(f"[CLEAN] removed {path}")
        except Exception as e:
            print(f"[WARN] cleanup failed for {path}: {e}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Generate summary-scope-break tests and validate on golden patch."
    )
    ap.add_argument("--dataset-dir", type=Path, default=_PROJECT_DATASET_PATCH_DIR)
    ap.add_argument("--summaries-dir", type=Path, default=_DEFAULT_SUMMARIES_DIR)
    ap.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    ap.add_argument("--issues", type=str, default="")
    ap.add_argument("--model", type=str, default=MODEL_NAME)
    ap.add_argument("--rounds", type=int, default=int(os.environ.get("LAB_SUMMARY_SCOPE_ROUNDS", "8")))
    ap.add_argument("--per-round", type=int, default=int(os.environ.get("LAB_SUMMARY_SCOPE_PER_ROUND", "4")))
    ap.add_argument("--max-accepted", type=int, default=int(os.environ.get("LAB_SUMMARY_SCOPE_MAX_ACCEPTED", "3")))
    ap.add_argument("--llm-timeout", type=float, default=float(os.environ.get("LAB_SUMMARY_SCOPE_LLM_TIMEOUT", "120")))
    ap.add_argument("-f", "--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args()


def process_issue(
    *,
    issue_id: str,
    dataset_dir: Path,
    summaries_dir: Path,
    output_dir: Path,
    client: OpenAI | None,
    model: str,
    rounds: int,
    per_round: int,
    max_accepted: int,
    llm_timeout: float,
    force: bool,
    dry_run: bool,
) -> None:
    ds_path = dataset_dir / f"{issue_id}.json"
    sum_path = summaries_dir / f"{issue_id}_summary.json"
    if not ds_path.is_file():
        print(f"[SKIP] missing dataset-patch: {ds_path}")
        return
    if not sum_path.is_file():
        print(f"[SKIP] missing summary: {sum_path}")
        return

    out_issue_dir = output_dir / _safe_path_part(issue_id)
    out_issue_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_issue_dir / "scope_break_tests.json"
    if out_path.exists() and not force:
        print(f"[SKIP] exists: {out_path} (use -f to overwrite)")
        return

    data = json.loads(ds_path.read_text(encoding="utf-8"))
    summary_data = json.loads(sum_path.read_text(encoding="utf-8"))
    summary = str(summary_data.get("summary", "")).strip()
    # Keep patch text exactly as-is; stripping trailing whitespace can corrupt hunks.
    source_patch = str(data.get("source_patch", ""))
    if not summary or not source_patch:
        print(f"[SKIP] {issue_id} missing summary/source_patch")
        return

    build_dir = Path(os.environ["LAB_LLVM_BUILD_DIR"]) / f"{issue_id}_summary_scope_golden"
    try:
        pass_name = _passes_pipeline_from_dataset(data)
        known_tests = _collect_known_tests(data)
        known_tests_text = _known_tests_to_text(known_tests)

        manifest: dict[str, Any] = {
            "issue_id": issue_id,
            "pr_number": data.get("pr_number"),
            "title": data.get("title", ""),
            "summary_path": str(sum_path),
            "dataset_patch_path": str(ds_path),
            "pass_name": pass_name,
            "model": model,
            "rounds": rounds,
            "per_round": per_round,
            "max_accepted": max_accepted,
            "tests": [],
        }

        if dry_run:
            manifest["dry_run"] = True
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] dry-run wrote {out_path}")
            return

        if client is None:
            raise RuntimeError("LLM client required")

        llvm_dir = rp.ensure_llvm_clone_for_issue(issue_id)
        ok, base_commit = _resolve_base_commit_from_commit_sha(
            llvm_dir, str(data.get("commit_sha", ""))
        )
        if not ok:
            print(f"[SKIP] {issue_id} {base_commit}")
            return

        additional_cmake_args = data.get("additional_cmake_args", [])
        if not isinstance(additional_cmake_args, list):
            additional_cmake_args = []

        print(f"[BUILD] {issue_id} golden -> {build_dir}")
        ok, err = _prepare_build(
            llvm_dir=llvm_dir,
            build_dir=build_dir,
            base_commit=base_commit,
            patch_text=source_patch,
            max_jobs=cfg.max_build_jobs,
            additional_cmake=additional_cmake_args,
        )
        if not ok:
            print(f"[FAIL] build {issue_id}: {err}")
            return
        manifest["build_dir"] = str(build_dir)

        accepted = manifest["tests"]
        seen = set()
        for rnd in range(1, rounds + 1):
            if len(accepted) >= max_accepted:
                break

            gen_prompt = GEN_USER_PROMPT_TEMPLATE.format(
                issue_id=issue_id,
                pr_number=data.get("pr_number"),
                title=str(data.get("title", "")),
                summary=summary,
                source_patch=source_patch,
                known_tests=known_tests_text,
                per_round=per_round,
            )
            try:
                raw = _call_model(
                    client,
                    model=model,
                    system=GEN_SYSTEM_PROMPT,
                    user=gen_prompt,
                    timeout=llm_timeout,
                )
            except Exception as e:
                print(f"[WARN] {issue_id} round {rnd} LLM generate failed: {e}")
                continue

            candidates = _parse_json_array(raw)
            print(f"[GEN] {issue_id} round {rnd}: parsed {len(candidates)} candidates")
            if not candidates:
                continue

            for c in candidates:
                if len(accepted) >= max_accepted:
                    break
                name = str(c.get("test_name", "")).strip() or f"scope_break_r{rnd}"
                src = str(c.get("source_program", "")).strip()
                exp = str(c.get("expect_optimized_program", "")).strip()
                reason = str(c.get("outside_scope_reason", "")).strip()
                if not src or not exp:
                    continue
                h = _sha256_text(src)
                if h in seen:
                    continue
                seen.add(h)

                llvm_helper.llvm_build_dir = str(build_dir)
                opt_ok, val_log = _validate_candidate(src, exp, pass_name)
                if not opt_ok:
                    continue

                accepted.append(
                    {
                        "test_name": name,
                        "source_program": src,
                        "expect_optimized_program": exp,
                        "outside_scope_reason": reason,
                        "llm_round": rnd,
                        "validation": val_log,
                    }
                )

            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[ROUND] {issue_id} {rnd}/{rounds}: accepted={len(accepted)}")

        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[DONE] {issue_id}: accepted={len(accepted)} -> {out_path}")
    finally:
        _cleanup_issue_dirs(issue_id, build_dir)


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()
    summaries_dir = args.summaries_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not dataset_dir.is_dir():
        raise SystemExit(f"[ERROR] dataset-dir not found: {dataset_dir}")
    if not summaries_dir.is_dir():
        raise SystemExit(f"[ERROR] summaries-dir not found: {summaries_dir}")

    issue_ids = _build_issue_list(summaries_dir, args.issues)
    if not issue_ids:
        raise SystemExit("[ERROR] no issues to process")

    client = None if args.dry_run else get_client()
    if not args.dry_run and not TOKEN and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("[ERROR] set LAB_LLM_TOKEN or OPENAI_API_KEY")
    print(f"[INFO] dataset-dir:   {dataset_dir}")
    print(f"[INFO] summaries-dir: {summaries_dir}")
    print(f"[INFO] output-dir:    {output_dir}")
    print(f"[INFO] model:         {args.model}")
    print(f"[INFO] issues:        {len(issue_ids)}")

    for issue_id in issue_ids:
        process_issue(
            issue_id=issue_id,
            dataset_dir=dataset_dir,
            summaries_dir=summaries_dir,
            output_dir=output_dir,
            client=client,
            model=args.model,
            rounds=max(1, int(args.rounds)),
            per_round=max(1, int(args.per_round)),
            max_accepted=max(1, int(args.max_accepted)),
            llm_timeout=max(5.0, float(args.llm_timeout)),
            force=bool(args.force),
            dry_run=bool(args.dry_run),
        )


if __name__ == "__main__":
    main()
