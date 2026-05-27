#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比较同一 issue 的两个 patch 在 llvm-opt-benchmark 上的命中次数。

流程：
1. 从 results/agent/.../fix.json 读取 agent patch；
2. 从 dataset/<issue>.json 读取官方 patch；
3. 分别构建两个 opt；
4. 读取本地 benchmark 指定 project 目录下的 *.ll；
5. 统计 opt 输出中的命中字符串次数（由 patch 中 print/errs 决定）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_PROJECT_DATASET_DIR = _PROJECT_ROOT / "dataset"

# 与现有 agent 脚本保持一致：强制使用仓库内 dataset/
os.environ["LAB_DATASET_DIR"] = str(_PROJECT_DATASET_DIR)

sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(_PROJECT_ROOT / "agent"))

cfg = None
llvm_helper = None
LabEnvironment = None
rp = None
_SANITIZED_IR_CACHE: dict[str, Path] = {}


def _safe_path_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value)


def _load_runtime_modules() -> None:
    global cfg, llvm_helper, LabEnvironment, rp
    if llvm_helper is not None and LabEnvironment is not None and rp is not None:
        return

    from env_config import cfg as _cfg  # noqa: PLC0415

    _cfg.dataset_dir = str(_PROJECT_DATASET_DIR)
    os.environ.setdefault("LAB_LLVM_DIR", _cfg.llvm_dir)
    os.environ.setdefault("LAB_LLVM_BUILD_DIR", _cfg.llvm_build_dir)
    os.environ.setdefault("LAB_LLVM_ALIVE_TV", _cfg.alive_tv)
    os.environ.setdefault("LAB_LLVM_COST_TOOL", _cfg.cost_tool)
    os.environ["LAB_DATASET_DIR"] = _cfg.dataset_dir
    os.environ.setdefault("LAB_TMP_DIR", _cfg.tmp_dir)
    os.environ.setdefault("TMPDIR", os.environ.get("LAB_TMP_DIR", _cfg.tmp_dir))

    import llvm_helper as _llvm_helper  # noqa: PLC0415
    from lab_env import Environment as _LabEnvironment  # noqa: PLC0415
    import retest_patches as _rp  # noqa: PLC0415

    cfg = _cfg
    llvm_helper = _llvm_helper
    LabEnvironment = _LabEnvironment
    rp = _rp


def _strip_diff_only(patch: str) -> str:
    idx = patch.find("diff --git")
    if idx >= 0:
        return patch[idx:]
    return patch


def _escape_cpp_string_literal(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _rewrite_hunk_header(
    hunk_header: str, *, new_start_delta: int = 0, new_count_delta: int = 0
) -> str:
    newline = "\n" if hunk_header.endswith("\n") else ""
    body = hunk_header[:-1] if newline else hunk_header
    m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$", body)
    if m is None:
        raise ValueError(f"非法 hunk header: {hunk_header!r}")

    old_start, old_count, new_start, new_count, suffix = m.groups()
    old_count_i = int(old_count) if old_count is not None else 1
    new_start_i = int(new_start) + new_start_delta
    new_count_i = int(new_count) if new_count is not None else 1
    bumped_new_count = new_count_i + new_count_delta

    old_part = f"-{old_start}" if old_count is None else f"-{old_start},{old_count_i}"
    if new_count is None and bumped_new_count == 1:
        new_part = f"+{new_start_i}"
    else:
        new_part = f"+{new_start_i},{bumped_new_count}"
    return f"@@ {old_part} {new_part} @@{suffix}{newline}"


def _inject_hit_print_to_all_hunks(patch_text: str, hit_pattern: str) -> str:
    """在 patch 每个 hunk 内尽量安全地插入一条 llvm::errs() 命中打印。"""
    normalized = _strip_diff_only(patch_text)
    lines = normalized.splitlines(keepends=True)
    if not lines:
        raise ValueError("patch 为空，无法注入 hit print")

    literal = _escape_cpp_string_literal(hit_pattern)
    file_new_line_delta = 0
    has_hunk = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            file_new_line_delta = 0
            i += 1
            continue
        if not line.startswith("@@"):
            i += 1
            continue

        has_hunk = True
        lines[i] = _rewrite_hunk_header(lines[i], new_start_delta=file_new_line_delta)
        start = i + 1
        end = start
        while end < len(lines) and not lines[end].startswith(("diff --git ", "@@")):
            end += 1

        anchor = -1
        indent = ""
        # 优先选择靠前的安全锚点，尽量保留 hunk 末尾 context，降低 apply 失败概率。
        for prefix in (" ", "+"):
            for j in range(start, end):
                body_line = lines[j]
                if not body_line.startswith(prefix):
                    continue
                code = body_line[1:]
                stripped = code.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    continue
                if stripped.endswith(("&&", "||", ",")):
                    continue
                if not (stripped.endswith(";") or stripped.endswith("{")):
                    continue
                has_trailing_context = any(
                    lines[k].startswith(" ") for k in range(j + 1, end)
                )
                if not has_trailing_context:
                    continue
                anchor = j
                indent = re.match(r"\s*", code).group(0)
                break
            if anchor >= 0:
                break

        if anchor < 0:
            print(f"[WARN] skip hit-print injection for hunk header: {lines[i].strip()}")
            i = end
            continue

        lines[i] = _rewrite_hunk_header(lines[i], new_count_delta=1)
        inject_line = f'+{indent}llvm::errs() << "{literal}\\\\n";\n'
        lines.insert(anchor + 1, inject_line)
        file_new_line_delta += 1
        i = end + 1

    if not has_hunk:
        raise ValueError("patch 不包含 hunk，无法注入 hit print")
    return "".join(lines)


def _component_to_pass(component_list: list[str] | None) -> str:
    mapping = getattr(llvm_helper, "_COMPONENT_TO_OPT_PASS", {})
    if not component_list:
        return mapping.get("InstCombine", "instcombine")
    return mapping.get(component_list[0], "instcombine")


def _normalize_pass_pipeline(pass_name: str) -> str:
    """标准化 opt --passes 参数，避免 instcombine 默认 fixpoint 验证导致 fatal。"""
    raw = pass_name.strip()
    if raw == "instcombine":
        return "instcombine<no-verify-fixpoint>"
    return raw


def _resolve_patch_files_with_timestamp(
    patch_root: str, issue_ids: list[str] | None, patch_ts: str
) -> list[tuple[str, str, str]]:
    """与 discriminating_tests.py 对齐：固定时间戳解析 issue -> fix.json。"""
    if not os.path.isdir(patch_root):
        print(f"[ERROR] Patch dir not found: {patch_root}")
        return []

    target_ts = patch_ts.strip()
    if not target_ts:
        return []

    def _fix_at(issue_name: str, issue_dir: str) -> tuple[str, str, str] | None:
        fix_path = os.path.join(issue_dir, target_ts, "fix.json")
        if os.path.isfile(fix_path):
            return issue_name, fix_path, target_ts
        return None

    items: list[tuple[str, str, str]] = []
    if issue_ids is None:
        direct_issue_name = os.path.basename(os.path.abspath(patch_root))
        direct = _fix_at(direct_issue_name, patch_root)
        if direct is not None:
            return [direct]

        for issue_name in sorted(os.listdir(patch_root)):
            issue_dir = os.path.join(patch_root, issue_name)
            if not os.path.isdir(issue_dir):
                continue
            if issue_name.startswith("retest-"):
                continue
            matched = _fix_at(issue_name, issue_dir)
            if matched is not None:
                items.append(matched)
        return items

    for raw in issue_ids:
        issue_id = raw.strip()
        if issue_id.endswith(".json"):
            issue_id = issue_id[:-5]
        if not issue_id:
            continue

        candidates = [
            (issue_id, os.path.join(patch_root, issue_id)),
            (_safe_path_part(issue_id), os.path.join(patch_root, _safe_path_part(issue_id))),
        ]
        found = False
        for issue_name, issue_dir in candidates:
            if not os.path.isdir(issue_dir):
                continue
            matched = _fix_at(issue_name, issue_dir)
            if matched is not None:
                items.append(matched)
                found = True
                break
            found = True
            print(
                f"[WARN] Timestamp dir not found for {raw}: "
                f"{os.path.join(issue_dir, target_ts, 'fix.json')}"
            )
            break

        if found:
            continue

        safe_issue = _safe_path_part(issue_id)
        suffixes = (f"-{issue_id}", f"-{safe_issue}")
        matched_issue_dirs: list[tuple[str, str]] = []
        for issue_name in sorted(os.listdir(patch_root)):
            issue_dir = os.path.join(patch_root, issue_name)
            if not os.path.isdir(issue_dir):
                continue
            if issue_name.endswith(suffixes):
                matched_issue_dirs.append((issue_name, issue_dir))

        if len(matched_issue_dirs) == 1:
            issue_name, issue_dir = matched_issue_dirs[0]
            matched = _fix_at(issue_name, issue_dir)
            if matched is not None:
                items.append(matched)
            else:
                print(
                    f"[WARN] Timestamp dir not found for {raw}: "
                    f"{os.path.join(issue_dir, target_ts, 'fix.json')}"
                )
        elif len(matched_issue_dirs) > 1:
            print(
                f"[WARN] Ambiguous historical issue dirs for {raw}: "
                + ", ".join(name for name, _ in matched_issue_dirs)
                + ". Please set --patch-root to one model root."
            )
        else:
            print(f"[WARN] Issue dir not found for {raw} in {patch_root}")

    return items


def _prepare_build(
    *,
    llvm_dir: str,
    build_dir: str,
    base_commit: str,
    patch_text: str,
    max_jobs: int,
    additional_cmake: list[str],
) -> tuple[bool, str]:
    llvm_helper.llvm_dir = llvm_dir
    llvm_helper.llvm_build_dir = build_dir
    os.environ["LAB_LLVM_DIR"] = llvm_dir
    llvm_helper.reset(base_commit)
    ok, log = llvm_helper.apply(_strip_diff_only(patch_text))
    if not ok:
        return False, f"apply failed: {log}"
    os.makedirs(build_dir, exist_ok=True)
    res, blog = llvm_helper.build(max_jobs, additional_cmake)
    if not res:
        return False, f"build failed: {blog[-4000:]}"
    return True, ""


def _list_local_project_ir_files(
    benchmark_root: str, project: str, max_files: int
) -> list[Path]:
    project_dir = Path(benchmark_root) / project
    if not project_dir.is_dir():
        raise FileNotFoundError(f"project 目录不存在: {project_dir}")
    files = sorted(project_dir.rglob("*.ll"))
    if not files:
        raise RuntimeError(f"{project_dir} 下未找到 .ll 文件")
    return files[:max_files]


def _run_opt_count_hits(
    *,
    opt_bin: str,
    ir_file: Path,
    pass_pipeline: str,
    hit_pattern: str,
    timeout_sec: float,
    sanitize_ir_attrs: bool,
) -> dict[str, Any]:
    cmd = [opt_bin, f"--passes={pass_pipeline}", "-disable-output", str(ir_file)]

    def _run_once(run_cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            run_cmd,
            capture_output=True,
            check=False,
            timeout=timeout_sec,
        )

    def _merged_output(proc: subprocess.CompletedProcess[bytes]) -> str:
        return (proc.stdout or b"").decode(errors="replace") + (
            (proc.stderr or b"").decode(errors="replace")
        )

    def _needs_attr_sanitize(merged: str) -> bool:
        return (
            "expected ')' at end of argument list" in merged
            and "captures(" in merged
        )

    def _sanitize_ir_for_legacy_opt(path: Path) -> Path:
        key = str(path.resolve())
        cached = _SANITIZED_IR_CACHE.get(key)
        if cached is not None and cached.is_file():
            return cached

        src = path.read_text(encoding="utf-8", errors="replace")
        # 老版本 LLVM 可能无法解析 captures(...) 参数属性；比较时可安全移除。
        sanitized = re.sub(r"\s+captures\([^)\n]*\)", "", src)
        if sanitized == src:
            _SANITIZED_IR_CACHE[key] = path
            return path

        tmp_root = Path(
            os.environ.get(
                "LAB_TMP_DIR",
                tempfile.gettempdir(),
            )
        )
        out_dir = tmp_root / "llvm-opt-benchmark-sanitized-ir"
        out_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        out_path = out_dir / f"{digest}-{path.name}"
        out_path.write_text(sanitized, encoding="utf-8")
        _SANITIZED_IR_CACHE[key] = out_path
        return out_path

    try:
        proc = _run_once(cmd)
    except Exception as e:
        return {
            "ok": False,
            "returncode": -1,
            "hits": 0,
            "error": str(e),
            "cmd": cmd,
        }
    merged = _merged_output(proc)
    used_sanitized_ir = False

    if sanitize_ir_attrs and proc.returncode != 0 and _needs_attr_sanitize(merged):
        try:
            sanitized_ir = _sanitize_ir_for_legacy_opt(ir_file)
            if sanitized_ir != ir_file:
                used_sanitized_ir = True
                cmd = [
                    opt_bin,
                    f"--passes={pass_pipeline}",
                    "-disable-output",
                    str(sanitized_ir),
                ]
                proc = _run_once(cmd)
                merged = _merged_output(proc)
        except Exception as e:
            merged += f"\n[SANITIZE_RETRY_ERROR] {e}"

    try:
        hits = len(re.findall(hit_pattern, merged))
    except re.error as e:
        raise ValueError(f"hit pattern 非法正则: {e}") from e
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "hits": hits,
        "output_sample": merged[:2000],
        "cmd": cmd,
        "used_sanitized_ir": used_sanitized_ir,
    }


def _load_patch_pair(
    patch_issue_id: str, fix_path: str, require_fast_pass: bool
) -> tuple[str, dict[str, Any], str]:
    with open(fix_path, "r", encoding="utf-8") as f:
        fix_data = json.load(f)
    if require_fast_pass and fix_data.get("fast_check_pass") is not True:
        raise RuntimeError("fix.json 的 fast_check_pass 不是 true")

    test_issue_id = rp.resolve_test_issue_id(patch_issue_id, str(_PROJECT_DATASET_DIR))
    if not test_issue_id:
        raise RuntimeError(f"无法解析 dataset issue: {patch_issue_id}")
    ds_path = _PROJECT_DATASET_DIR / f"{test_issue_id}.json"
    with open(ds_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    if not dataset.get("verified", False):
        raise RuntimeError(f"{test_issue_id} dataset 未 verified")

    patches = rp._collect_patches_to_test(fix_data)
    if not patches:
        raise RuntimeError("fix.json 中没有可用 patch")
    agent_patch = patches[0]["patch"]
    dataset_patch = dataset.get("patch") or ""
    if not dataset_patch.strip():
        raise RuntimeError(f"dataset/{test_issue_id}.json 缺少 patch 字段")
    return test_issue_id, dataset, agent_patch + "\n", dataset_patch + "\n"


def _default_output_path(patch_issue_id: str, patch_ts: str, project: str) -> Path:
    return (
        _PROJECT_ROOT
        / "results"
        / "llvm_opt_benchmark_hits"
        / _safe_path_part(patch_issue_id)
        / _safe_path_part(patch_ts)
        / f"{_safe_path_part(project)}.json"
    )


def _resolve_output_path(
    output_arg: str,
    *,
    patch_issue_id: str,
    patch_ts: str,
    project: str,
    multi_issue: bool,
) -> Path:
    if not output_arg.strip():
        return _default_output_path(patch_issue_id, patch_ts, project)

    out = Path(output_arg)
    if multi_issue:
        if out.suffix.lower() == ".json":
            raise ValueError("多 issue 模式下 --output 必须是目录路径，不能是单个 .json 文件")
        return (
            out
            / _safe_path_part(patch_issue_id)
            / _safe_path_part(patch_ts)
            / f"{_safe_path_part(project)}.json"
        )
    return out


def _process_issue(
    *,
    patch_issue_id: str,
    fix_path: str,
    patch_ts: str,
    args: argparse.Namespace,
    files: list[Path],
    multi_issue: bool,
) -> tuple[bool, str]:
    test_issue_id, dataset, agent_patch, dataset_patch = _load_patch_pair(
        patch_issue_id, fix_path, args.require_fast_pass
    )
    agent_pat = args.agent_hit_pattern.strip() or args.hit_pattern
    dataset_pat = args.dataset_hit_pattern.strip() or args.hit_pattern

    components = (dataset.get("hints") or {}).get("components")
    pass_name = args.pass_name.strip() or _component_to_pass(
        components if isinstance(components, list) else None
    )
    pass_pipeline = _normalize_pass_pipeline(pass_name)
    print(
        f"[INFO] issue={patch_issue_id} -> dataset={test_issue_id}, "
        f"pass={pass_name}, pipeline={pass_pipeline}"
    )

    issue_llvm_dir = rp.ensure_llvm_clone_for_issue(test_issue_id)
    build_root = Path(os.environ.get("LAB_LLVM_BUILD_DIR", cfg.llvm_build_dir))
    build_agent = build_root / f"{test_issue_id}_benchmark_agent"
    build_dataset = build_root / f"{test_issue_id}_benchmark_dataset"

    env = LabEnvironment(
        test_issue_id, os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")
    )

    agent_patch = _inject_hit_print_to_all_hunks(agent_patch, agent_pat)
    dataset_patch = _inject_hit_print_to_all_hunks(dataset_patch, dataset_pat)

    print(f"[BUILD] agent patch -> {build_agent}")
    ok, err = _prepare_build(
        llvm_dir=issue_llvm_dir,
        build_dir=str(build_agent),
        base_commit=env.base_commit,
        patch_text=agent_patch,
        max_jobs=env.max_build_jobs,
        additional_cmake=env.additional_cmake_args,
    )
    if not ok:
        raise RuntimeError(f"agent 构建失败: {err}")

    print(f"[BUILD] dataset patch -> {build_dataset}")
    ok, err = _prepare_build(
        llvm_dir=issue_llvm_dir,
        build_dir=str(build_dataset),
        base_commit=env.base_commit,
        patch_text=dataset_patch,
        max_jobs=env.max_build_jobs,
        additional_cmake=env.additional_cmake_args,
    )
    if not ok:
        raise RuntimeError(f"dataset 构建失败: {err}")

    agent_opt = str(build_agent / "bin" / "opt")
    dataset_opt = str(build_dataset / "bin" / "opt")
    if not Path(agent_opt).is_file():
        raise FileNotFoundError(f"opt 不存在: {agent_opt}")
    if not Path(dataset_opt).is_file():
        raise FileNotFoundError(f"opt 不存在: {dataset_opt}")

    per_file: list[dict[str, Any]] = []
    total_agent_hits = 0
    total_dataset_hits = 0

    for ir in files:
        r_agent = _run_opt_count_hits(
            opt_bin=agent_opt,
            ir_file=ir,
            pass_pipeline=pass_pipeline,
            hit_pattern=agent_pat,
            timeout_sec=args.opt_timeout,
            sanitize_ir_attrs=args.sanitize_ir_attrs,
        )
        r_dataset = _run_opt_count_hits(
            opt_bin=dataset_opt,
            ir_file=ir,
            pass_pipeline=pass_pipeline,
            hit_pattern=dataset_pat,
            timeout_sec=args.opt_timeout,
            sanitize_ir_attrs=args.sanitize_ir_attrs,
        )
        total_agent_hits += int(r_agent["hits"])
        total_dataset_hits += int(r_dataset["hits"])
        per_file.append(
            {
                "file": str(ir),
                "agent": r_agent,
                "dataset": r_dataset,
            }
        )

    winner = "tie"
    if total_agent_hits > total_dataset_hits:
        winner = "agent"
    elif total_dataset_hits > total_agent_hits:
        winner = "dataset"

    result = {
        "patch_issue_id": patch_issue_id,
        "test_issue_id": test_issue_id,
        "patch_timestamp": patch_ts,
        "patch_root": args.patch_root,
        "project": args.project,
        "file_limit": args.file_limit,
        "pass_name": pass_name,
        "pass_pipeline": pass_pipeline,
        "agent_hit_pattern": agent_pat,
        "dataset_hit_pattern": dataset_pat,
        "sanitize_ir_attrs": args.sanitize_ir_attrs,
        "build_agent_dir": str(build_agent),
        "build_dataset_dir": str(build_dataset),
        "total_agent_hits": total_agent_hits,
        "total_dataset_hits": total_dataset_hits,
        "winner": winner,
        "per_file": per_file,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    out_path = _resolve_output_path(
        args.output,
        patch_issue_id=patch_issue_id,
        patch_ts=patch_ts,
        project=args.project,
        multi_issue=multi_issue,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[DONE] issue={patch_issue_id} output -> {out_path}")
    print(
        "[SUMMARY] "
        f"issue={patch_issue_id}, "
        f"agent_hits={total_agent_hits}, "
        f"dataset_hits={total_dataset_hits}, "
        f"winner={winner}"
    )
    return True, patch_issue_id


def main() -> None:
    ap = argparse.ArgumentParser(
        description="比较 agent patch 与 dataset patch 在 llvm-opt-benchmark 上的 hit 次数"
    )
    ap.add_argument(
        "--patch-root",
        default=str(_PROJECT_ROOT / "results" / "agent" / "qwen3.5-plus"),
        help="agent patch 根目录（含 <issue>/<ts>/fix.json）",
    )
    ap.add_argument(
        "--issues",
        default="",
        help="逗号分隔 issue id；默认处理 patch-root 下全部可处理 issue",
    )
    ap.add_argument("--patch-ts", required=True, help="指定 patch 时间戳目录（必填）")
    ap.add_argument("--project", required=True, help="benchmark project 名，例如 arrow/duckdb")
    ap.add_argument(
        "--benchmark-root",
        default=str(_PROJECT_ROOT / "llvm-opt-benchmark" / "bench"),
        help="本地 benchmark 根目录（包含 <project>/*.ll）",
    )
    ap.add_argument("--file-limit", type=int, default=5000, help="最多读取并测试多少个 .ll")
    ap.add_argument(
        "--hit-pattern",
        default="PATCH_HIT",
        help="命中计数正则；若未单独指定 agent/dataset pattern，则两边都用它",
    )
    ap.add_argument("--agent-hit-pattern", default="", help="agent 侧命中计数正则（可选）")
    ap.add_argument("--dataset-hit-pattern", default="", help="dataset 侧命中计数正则（可选）")
    ap.add_argument("--pass-name", default="", help="显式指定 opt pass（如 instcombine）")
    ap.add_argument(
        "--require-fast-pass",
        action="store_true",
        help="要求 fix.json 的 fast_check_pass==true",
    )
    ap.add_argument("--opt-timeout", type=float, default=60.0, help="单个 .ll 运行 opt 超时秒数")
    ap.add_argument(
        "--sanitize-ir-attrs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="遇到老版本 opt 解析失败时，自动移除不兼容 IR 参数属性（默认开启）",
    )
    ap.add_argument(
        "--output",
        default="",
        help="输出 JSON 路径（默认 results/llvm_opt_benchmark_hits/...）",
    )
    args = ap.parse_args()
    _load_runtime_modules()

    if args.file_limit <= 0:
        raise ValueError("--file-limit 必须 > 0")

    issue_list = [x.strip() for x in args.issues.split(",") if x.strip()]
    issue_filter = issue_list if issue_list else None
    items = _resolve_patch_files_with_timestamp(args.patch_root, issue_filter, args.patch_ts)

    if not items:
        print(f"[WARN] 没有可处理的 fix.json（未匹配到 patch_ts={args.patch_ts.strip()}）")
        return

    files = _list_local_project_ir_files(
        args.benchmark_root, args.project.strip(), args.file_limit
    )
    print(
        f"[INFO] loaded {len(files)} local .ll files from "
        f"{Path(args.benchmark_root) / args.project.strip()}"
    )

    ok_count = 0
    fail_count = 0
    multi_issue = len(items) > 1
    for patch_issue_id, fix_path, patch_ts in items:
        try:
            _ok, _ = _process_issue(
                patch_issue_id=patch_issue_id,
                fix_path=fix_path,
                patch_ts=patch_ts,
                args=args,
                files=files,
                multi_issue=multi_issue,
            )
            ok_count += 1
        except Exception as e:
            fail_count += 1
            print(f"[FAIL] issue={patch_issue_id}: {e}")

    print(f"[ALL DONE] total={len(items)}, success={ok_count}, failed={fail_count}")


if __name__ == "__main__":
    main()
