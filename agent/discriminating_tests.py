#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 LLM 生成「能区分 agent patch 与 dataset patch」的 LLVM IR 测试用例并严格校验。

判定标准（与 scripts/llvm_helper.py 一致）：
- 在 **agent patch** 构建的 opt 上：Alive2(source → opt 输出) 通过，且 cost_check 通过
  （current 代价同时不劣于 source 与 expect）。
- 在 **dataset（官方）patch** 构建的 opt 上：cost_check **不通过**（官方版本在你给出的 expect 意义下
  达不到同等优化），且 Alive2(source → opt 输出) 仍通过（说明 opt 输出是合法精化，避免 IR 损坏）。

对每个模型循环 LLM_ROUNDS 轮（默认 15），每轮生成若干候选；仅累积最终通过双构建验证的用例。

用法:
  cp agent/.env.example agent/.env   # 填写 LAB_LLVM_* 等（与 agent/run.py 相同）
  python agent/discriminating_tests.py --patch-root results/agent/qwen3.5-plus --models qwen3.5-plus

输出写入 results/discriminating_tests_llmgen/<patch-root 末级目录名>/<issue>/<patch 时间戳>/accepted_tests.json，
与 results/agent/.../<issue>/<ts>/fix.json 一一对应，不同时间戳互不覆盖。

环境:
  见 agent/env_config.py；另需 LAB_LLM_URL / LAB_LLM_TOKEN / LAB_LLM_TEMP（或 .env）
  LAB_DISCRIM_LLM_ROUNDS (默认 15)
  LAB_DISCRIM_PER_ROUND (默认每轮 4 条候选)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
_PROJECT_DATASET_DIR = os.path.join(_PROJECT_ROOT, "dataset")

# 与 run.py 一致：强制仅使用仓库内 dataset/；须在导入 env_config / llvm_helper 之前设置。
os.environ["LAB_DATASET_DIR"] = _PROJECT_DATASET_DIR

sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))
sys.path.insert(0, _THIS_DIR)

from env_config import cfg  # noqa: E402

cfg.dataset_dir = _PROJECT_DATASET_DIR


def _ensure_writable_tmp_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    if not os.access(path, os.W_OK | os.X_OK):
        raise PermissionError(f"LAB_TMP_DIR is not writable: {path}")
    return path


os.environ.setdefault("LAB_LLVM_DIR", cfg.llvm_dir)
os.environ.setdefault("LAB_LLVM_BUILD_DIR", cfg.llvm_build_dir)
os.environ["LAB_DATASET_DIR"] = cfg.dataset_dir
os.environ.setdefault("LAB_LLVM_ALIVE_TV", cfg.alive_tv)
os.environ.setdefault("LAB_LLVM_COST_TOOL", cfg.cost_tool)
_tmp_dir = os.environ.get("LAB_TMP_DIR", cfg.tmp_dir)
if not _tmp_dir:
    _tmp_dir = cfg.tmp_dir
_tmp_dir = _ensure_writable_tmp_dir(_tmp_dir)
os.environ["LAB_TMP_DIR"] = _tmp_dir
os.environ["TMPDIR"] = _tmp_dir

import llvm_helper  # noqa: E402
from lab_env import Environment as LabEnvironment  # noqa: E402
import retest_patches as rp  # noqa: E402

DATASET_DIR = cfg.dataset_dir
STOP_AFTER_ACCEPTED = 10


def _safe_path_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_patch_files_with_timestamp(
    patch_root: str, issue_ids: list[str] | None, patch_ts: str
) -> list[tuple[str, str, str]]:
    """Resolve to [(issue_id, patch_fix_json_path, run_timestamp)] for a fixed timestamp."""
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
    m = raw.strip()
    if not m:
        return m
    if _is_official_openai_endpoint(api_url) and "/" in m:
        return m.split("/", 1)[1]
    if m.startswith("custom_openai/"):
        return m.split("/", 1)[1]
    return m


def _strip_diff_only(patch: str) -> str:
    idx = patch.find("diff --git")
    if idx >= 0:
        return patch[idx:]
    return patch


def _component_to_pass(component_list: list[str] | None) -> str:
    m = getattr(llvm_helper, "_COMPONENT_TO_OPT_PASS", {})
    if not component_list:
        return m.get("InstCombine", "instcombine")
    return m.get(component_list[0], "instcombine")


def _strip_opt_module_prefix(text: str) -> str:
    return text.removeprefix(
        "; ModuleID = '<stdin>'\nsource_filename = \"<stdin>\"\n"
    ).removeprefix("\n")


def _run_opt(source_ll: str, pass_name: str) -> tuple[bool, str, str]:
    opt_bin = os.path.join(llvm_helper.llvm_build_dir, "bin/opt")
    if not os.path.isfile(opt_bin):
        return False, "", f"opt not found: {opt_bin}"
    cmd = [opt_bin, f"--passes={pass_name}", "-S"]
    try:
        proc = subprocess.run(
            cmd,
            input=source_ll.encode(),
            timeout=30.0,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode(errors="replace")
            return False, "", err or f"opt exit {proc.returncode}"
        out = proc.stdout.decode(errors="replace")
        return True, out, ""
    except Exception as e:
        return False, "", str(e)


def _eval_opt_side(
    source_program: str,
    expect_optimized_program: str,
    pass_name: str,
) -> dict[str, Any]:
    """在当前 llvm_helper.llvm_build_dir 上跑 opt + alive2 + cost，返回结构化日志。"""
    ok, output, err = _run_opt(source_program, pass_name)
    if not ok:
        return {
            "opt_ok": False,
            "opt_error": err,
            "alive2_ok": False,
            "cost_ok": False,
        }

    new_input = llvm_helper.copy_triple(source_program, output.encode())
    new_input = llvm_helper.copy_datalayout(new_input, output.encode())
    res_alive, log_alive = llvm_helper.alive2_check(
        new_input, output, additional_args=None
    )
    cur = _strip_opt_module_prefix(output)
    res_cost, costs = llvm_helper.cost_check(
        source_program, expect_optimized_program, cur
    )
    return {
        "opt_ok": True,
        "alive2_ok": bool(res_alive),
        "cost_ok": bool(res_cost),
        "alive2": log_alive,
        "costs": costs,
        "current_optimized_program": cur,
    }


def _validate_candidate(
    source_program: str,
    expect_optimized_program: str,
    pass_name: str,
) -> tuple[bool, dict[str, Any]]:
    """在当前 llvm_build_dir 上校验候选：opt、Alive2、cost 均通过则 ok 为 True。"""
    log = _eval_opt_side(source_program, expect_optimized_program, pass_name)
    ok = bool(
        log.get("opt_ok") and log.get("alive2_ok") and log.get("cost_ok")
    )
    return ok, log


def _parse_llm_json_array(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and "tests" in data:
        data = data["tests"]
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            out.append(item)
    return out


def _call_llm(
    *,
    model: str,
    system: str,
    user: str,
    temperature: float,
    timeout: float,
) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(f"需要安装 openai SDK: {e}") from e

    api_url = os.environ.get("LAB_LLM_URL", "https://api.openai.com/v1")
    token = os.environ.get("LAB_LLM_TOKEN", "")
    kwargs: dict[str, object] = {"base_url": api_url}
    if token:
        kwargs["api_key"] = token
    client = OpenAI(**kwargs)
    name = _api_model_name(model, api_url)
    resp = client.chat.completions.create(
        model=name,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        timeout=timeout,
    )
    content = resp.choices[0].message.content
    return content if isinstance(content, str) else str(content or "")


def build_discriminating_prompt(
    *,
    issue_id: str,
    dataset: dict[str, Any],
    agent_patch_excerpt: str,
    dataset_patch_excerpt: str,
    accepted_summaries: list[str],
    per_round: int,
) -> tuple[str, str]:
    title = (dataset.get("issue") or {}).get("title", "")
    body = (dataset.get("issue") or {}).get("body", "")
    hints = json.dumps(dataset.get("hints") or {}, ensure_ascii=False, indent=2)[:8000]

    sys_p = """You are an expert in LLVM IR and InstCombine. Your task is to design "missed optimization" tests: provide valid LLVM IR as `source` and the expected IR after optimization as `expect` (same function, semantically equivalent).
Each test case should have the following fields:
- test_name: short identifier
- source_program: full LLVM IR (may include target datalayout; at least one `define`)
- expect_optimized_program: IR semantically equivalent to source that reflects stronger optimization

"""

    acc = "\n".join(f"- {x}" for x in accepted_summaries[-20:]) or "(none yet)"
    user_p = f"""Issue #{issue_id}: {title}

Issue description:
{body}

Hints:
{hints}

[Agent patch] (may be more aggressive or differ in conditions)
{agent_patch_excerpt}

[Official patch]
{dataset_patch_excerpt}

Test cases already found and validated (do not repeat similar patterns):
{acc}

Please generate {per_round} more **pairwise distinct** test cases (JSON array).
For each issue, the generated test case is required to be optimizable by the agent patch, but not optimizable by the official patch.
Please first analyze the differences in optimization patterns between the agent patch and the official patch, and then generate test cases that meet the requirements based on those differences.

You should output your analysis and the generated test cases in a structured format as follows:

Analysis: <your analysis here>

Test cases:
```json
[
    {{
        "test_name": <test name here>,
        "source_program": <source program here>,
        "expect_optimized_program": <expect optimized program here>
    }}
]
```
"""

    return sys_p, user_p


def prepare_build(
    *,
    llvm_dir: str,
    build_dir: str,
    base_commit: str,
    patch_text: str,
    max_jobs: int,
    additional_cmake: list[str],
) -> tuple[bool, str]:
    build_meta_path = os.path.join(build_dir, ".disc_build_meta.json")
    normalized_patch = _strip_diff_only(patch_text)
    build_fingerprint = {
        "base_commit": base_commit,
        "patch_sha256": _sha256_text(normalized_patch),
        "additional_cmake": additional_cmake,
        "llvm_dir": llvm_dir,
    }

    opt_bin = os.path.join(build_dir, "bin", "opt")
    if os.path.isfile(opt_bin) and os.path.isfile(build_meta_path):
        try:
            with open(build_meta_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            if old == build_fingerprint:
                return True, "reuse existing build"
        except Exception:
            pass

    llvm_helper.llvm_dir = llvm_dir
    llvm_helper.llvm_build_dir = build_dir
    os.environ["LAB_LLVM_DIR"] = llvm_dir
    llvm_helper.reset(base_commit)
    ok, log = llvm_helper.apply(normalized_patch)
    if not ok:
        return False, f"apply failed: {log}"
    os.makedirs(build_dir, exist_ok=True)
    res, blog = llvm_helper.build(max_jobs, additional_cmake)
    if not res:
        return False, f"build failed: {blog[-4000:]}"
    try:
        with open(build_meta_path, "w", encoding="utf-8") as f:
            json.dump(build_fingerprint, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return True, ""


def process_issue(
    *,
    patch_issue_id: str,
    fix_path: str,
    patch_ts: str,
    patch_root: str,
    models: list[str],
    rounds: int,
    per_round: int,
    require_fast_pass: bool,
    override: bool,
    llm_timeout: float,
) -> None:
    test_issue_id = rp.resolve_test_issue_id(patch_issue_id, DATASET_DIR)
    if not test_issue_id:
        print(f"[SKIP] 无法解析 dataset issue: {patch_issue_id}")
        return

    ds_path = os.path.join(DATASET_DIR, f"{test_issue_id}.json")
    with open(ds_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    if not dataset.get("verified", False):
        print(f"[SKIP] {test_issue_id} dataset 未 verified")
        return

    with open(fix_path, "r", encoding="utf-8") as f:
        fix_data = json.load(f)

    if require_fast_pass and fix_data.get("fast_check_pass") is not True:
        print(f"[SKIP] {patch_issue_id} fast_check_pass 非 true")
        return

    patches = rp._collect_patches_to_test(fix_data)
    if not patches:
        print(f"[SKIP] {patch_issue_id} 无可用 patch")
        return

    agent_patch = patches[0]["patch"]
    dataset_patch = dataset.get("patch") or ""
    if not dataset_patch.strip():
        print(f"[SKIP] {test_issue_id} dataset 缺少 patch 字段")
        return

    hints = dataset.get("hints") or {}
    components = hints.get("components")
    pass_name = _component_to_pass(
        components if isinstance(components, list) else None
    )

    llvm_dir = rp.ensure_llvm_clone_for_issue(test_issue_id)
    base_root = os.environ["LAB_LLVM_BUILD_DIR"]
    build_agent = os.path.join(base_root, f"{test_issue_id}_disc_agent")
    build_dataset = os.path.join(base_root, f"{test_issue_id}_disc_dataset")

    try:
        env = LabEnvironment(test_issue_id, os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z"))
    except Exception as e:
        print(f"[SKIP] LabEnvironment 初始化失败 {test_issue_id}: {e}")
        return

    print(f"[BUILD] {test_issue_id} agent side -> {build_agent}")
    ok, err = prepare_build(
        llvm_dir=llvm_dir,
        build_dir=build_agent,
        base_commit=env.base_commit,
        patch_text=agent_patch,
        max_jobs=env.max_build_jobs,
        additional_cmake=env.additional_cmake_args,
    )
    if not ok:
        print(f"[FAIL] agent 构建: {err}")
        return

    print(f"[BUILD] {test_issue_id} dataset side -> {build_dataset}")
    ok, err = prepare_build(
        llvm_dir=llvm_dir,
        build_dir=build_dataset,
        base_commit=env.base_commit,
        patch_text=dataset_patch,
        max_jobs=env.max_build_jobs,
        additional_cmake=env.additional_cmake_args,
    )
    if not ok:
        print(f"[FAIL] dataset 构建: {err}")
        return

    safe_model_root = _safe_path_part(os.path.basename(os.path.normpath(patch_root)))
    out_dir = os.path.join(
        _PROJECT_ROOT,
        "results",
        "discriminating_tests_llmgen",
        safe_model_root,
        _safe_path_part(patch_issue_id),
    )
    ts_part = (patch_ts or "").strip()
    if ts_part:
        out_dir = os.path.join(out_dir, _safe_path_part(ts_part))
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, "accepted_tests.json")
    if os.path.isfile(manifest_path) and not override:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {
            "patch_issue_id": patch_issue_id,
            "test_issue_id": test_issue_id,
            "patch_source": fix_path,
            "patch_timestamp": patch_ts,
            "build_agent_dir": build_agent,
            "build_dataset_dir": build_dataset,
            "pass_name": pass_name,
            "tests": [],
        }

    accepted: list[dict[str, Any]] = manifest.setdefault("tests", [])
    seen = {hash(t.get("source_program", "")) for t in accepted}

    temp = float(os.environ.get("LAB_LLM_TEMP", "0.6"))
    if len(accepted) >= STOP_AFTER_ACCEPTED:
        print(
            f"[STOP] 已有通过用例 {len(accepted)} 条 (>= {STOP_AFTER_ACCEPTED})，按策略不再继续生成: {manifest_path}"
        )
        print(f"[DONE] {test_issue_id} 写入 {manifest_path}")
        return

    should_stop = False
    for model in models:
        if should_stop:
            break
        print(f"[LLM] model={model}")
        for rnd in range(1, rounds + 1):
            if should_stop:
                break
            summaries = [
                f"{t.get('test_name')}: {t.get('source_program', '')[:200]}"
                for t in accepted
            ]
            sys_p, user_p = build_discriminating_prompt(
                issue_id=test_issue_id,
                dataset=dataset,
                agent_patch_excerpt=agent_patch,
                dataset_patch_excerpt=dataset_patch,
                accepted_summaries=summaries,
                per_round=per_round,
            )
            try:
                reply = _call_llm(
                    model=model,
                    system=sys_p,
                    user=user_p,
                    temperature=temp,
                    timeout=llm_timeout,
                )
            except Exception as e:
                print(f"[WARN] round {rnd} LLM 调用失败: {e}")
                continue

            print(f"[LLM response] model={model} round={rnd}\n{reply}\n---")

            candidates = _parse_llm_json_array(reply)
            if not candidates:
                print(f"[WARN] round {rnd} 无法解析 JSON，跳过")
                continue
            print(f"[GEN] round {rnd} 生成 {len(candidates)} 个 test case:")
            for i, raw in enumerate(candidates, 1):
                gen_name = str(raw.get("test_name") or f"gen_r{rnd}_{i}")
                gen_src = (raw.get("source_program") or "").strip().replace("\n", "\\n")
                gen_exp = (
                    (raw.get("expect_optimized_program") or "")
                    .strip()
                    .replace("\n", "\\n")
                )
                print(
                    f"    - {gen_name} | source={gen_src} | expect={gen_exp}"
                )

            round_accepted = 0
            for raw in candidates:
                name = str(raw.get("test_name") or f"gen_r{rnd}_{round_accepted}")
                src = (raw.get("source_program") or "").strip()
                exp = (raw.get("expect_optimized_program") or "").strip()
                if not src or not exp:
                    continue
                h = hash(src)
                if h in seen:
                    continue

                llvm_helper.llvm_build_dir = build_agent
                ok_agent, log_agent = _validate_candidate(src, exp, pass_name)
                if not ok_agent:
                    continue

                llvm_helper.llvm_build_dir = build_dataset
                ok_ds_cost, log_ds = _validate_candidate(src, exp, pass_name)
                # 需要：dataset 侧 cost 不通过，但 alive2 仍通过（合法精化）
                ds_alive = bool(log_ds.get("alive2_ok"))
                ds_cost_ok = bool(log_ds.get("cost_ok"))
                if ds_cost_ok or not ds_alive:
                    continue

                entry = {
                    "test_name": name,
                    "source_program": src,
                    "expect_optimized_program": exp,
                    "llm_model": model,
                    "llm_round": rnd,
                    "validation": {
                        "agent": log_agent,
                        "dataset": log_ds,
                    },
                }
                accepted.append(entry)
                seen.add(h)
                round_accepted += 1
                if len(accepted) >= STOP_AFTER_ACCEPTED:
                    should_stop = True
                break

            manifest["last_model"] = model
            manifest["last_round"] = rnd
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            print(
                f"  round {rnd}/{rounds}: +{round_accepted} 通过，累计 {len(accepted)}"
            )
            if should_stop:
                print(
                    f"[STOP] 通过用例已达到 {len(accepted)} 条 (>= {STOP_AFTER_ACCEPTED})，终止后续轮次/模型"
                )
                break

    print(f"[DONE] {test_issue_id} 写入 {manifest_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成并校验区分 agent/dataset patch 的测试用例")
    ap.add_argument(
        "--patch-root",
        default=os.path.join(_PROJECT_ROOT, "results", "agent", "qwen3.5-plus"),
        help="agent 结果根目录（含 <issue>/<ts>/fix.json）",
    )
    ap.add_argument(
        "--issues",
        default="",
        help="逗号分隔 issue id；默认处理 patch-root 下全部含 fix.json 的 issue",
    )
    ap.add_argument(
        "--patch-ts",
        default="",
        help="仅处理指定时间戳目录（如 20260330-004829）；会扫描该时间戳对应的 run",
    )
    ap.add_argument(
        "--models",
        default=os.environ.get("LAB_DISCRIM_MODELS", ""),
        help="逗号分隔模型名；默认使用 LAB_LLM_MODEL",
    )
    ap.add_argument(
        "--rounds",
        type=int,
        default=int(os.environ.get("LAB_DISCRIM_LLM_ROUNDS", "15")),
        help="每个模型循环调用 LLM 次数",
    )
    ap.add_argument(
        "--per-round",
        type=int,
        default=int(os.environ.get("LAB_DISCRIM_PER_ROUND", "4")),
        help="每轮期望生成的候选条数（prompt 中说明）",
    )
    ap.add_argument(
        "--require-fast-pass",
        action="store_true",
        help="仅处理 fix.json 中 fast_check_pass==true 的 run",
    )
    ap.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="覆盖已有的 accepted_tests.json",
    )
    ap.add_argument(
        "--llm-timeout",
        type=float,
        default=float(os.environ.get("LAB_DISCRIM_LLM_TIMEOUT", "120")),
    )
    args = ap.parse_args()

    models_raw = args.models.strip()
    if models_raw:
        models = [x.strip() for x in models_raw.split(",") if x.strip()]
    else:
        m = os.environ.get("LAB_LLM_MODEL", "gpt-4o")
        models = [m]
    if not models:
        print("[ERROR] 未指定模型", file=sys.stderr)
        sys.exit(1)

    issue_list = [x.strip() for x in args.issues.split(",") if x.strip()]
    issue_filter = issue_list if issue_list else None

    if args.patch_ts.strip():
        items = _resolve_patch_files_with_timestamp(
            args.patch_root, issue_filter, args.patch_ts
        )
    elif issue_filter:
        items = []
        for iid in issue_filter:
            resolved = rp.resolve_patch_files(args.patch_root, [iid])
            items.extend(resolved)
    else:
        items = rp.resolve_patch_files(args.patch_root, None)

    if not items:
        if args.patch_ts.strip():
            print(f"[WARN] 没有可处理的 fix.json（未匹配到 patch_ts={args.patch_ts.strip()}）")
        else:
            print("[WARN] 没有可处理的 fix.json")
        return

    for patch_issue_id, fix_path, patch_ts in items:
        process_issue(
            patch_issue_id=patch_issue_id,
            fix_path=fix_path,
            patch_ts=patch_ts,
            patch_root=args.patch_root,
            models=models,
            rounds=args.rounds,
            per_round=args.per_round,
            require_fast_pass=args.require_fast_pass,
            override=args.force,
            llm_timeout=args.llm_timeout,
        )


if __name__ == "__main__":
    main()
