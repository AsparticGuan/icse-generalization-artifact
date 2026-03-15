#!/usr/bin/env python3
"""agent/run_batch.py — 按模型与测试用例批量执行 agent/run.py。

需求对应：
1) 支持一个或多个模型，严格按传入顺序执行；
2) 支持单个/多个/全部测试用例（issue），并对所有模型生效；
3) 模型通过 run.py --model 传参，不再改写 agent/.env；
4) 支持按序传递思考强度与模型私有 thinking/reasoning 参数；
5) 可选在每轮 run.py 结束后直接调用 agent/retest_patches.py 复测同批 patch。
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_ENV_PATH = _THIS_DIR / ".env"
_RUN_PATH = _THIS_DIR / "run.py"

_EFFORT_CHOICES = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def _safe_path_part(value: str) -> str:
    """将字符串转换为可用于目录名的安全片段。"""
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value)


def _split_models(part: str) -> list[str]:
    """将字符串拆分为模型列表（支持逗号分隔）。"""
    models: list[str] = []
    for item in part.split(","):
        model = item.strip()
        if model:
            models.append(model)
    return models


def _parse_models(model_args: list[str], models_csv: str) -> list[str]:
    """解析模型参数，保留传入顺序。"""
    raw_parts: list[str] = []
    raw_parts.extend(model_args)
    if models_csv.strip():
        raw_parts.append(models_csv)

    models: list[str] = []
    for part in raw_parts:
        models.extend(_split_models(part))
    return models


def _extract_models_in_cli_order(argv: list[str]) -> list[str]:
    """从原始 argv 提取模型，严格按参数出现顺序。

    支持：
    - --model <value>
    - -m <value>
    - --model=<value>
    - -m=<value>
    - --models <csv>
    - --models=<csv>
    """
    models: list[str] = []
    idx = 0
    while idx < len(argv):
        token = argv[idx]

        if token in ("--model", "-m"):
            if idx + 1 < len(argv):
                models.extend(_split_models(argv[idx + 1]))
                idx += 2
                continue
            idx += 1
            continue

        if token.startswith("--model="):
            models.extend(_split_models(token.split("=", 1)[1]))
            idx += 1
            continue

        if token.startswith("-m="):
            models.extend(_split_models(token.split("=", 1)[1]))
            idx += 1
            continue

        if token == "--models":
            if idx + 1 < len(argv):
                models.extend(_split_models(argv[idx + 1]))
                idx += 2
                continue
            idx += 1
            continue

        if token.startswith("--models="):
            models.extend(_split_models(token.split("=", 1)[1]))
            idx += 1
            continue

        idx += 1

    return models


def _normalize_issue_arg(raw: str) -> str | None:
    """将 issues 参数归一化为 run.py 可接受的形式。"""
    value = raw.strip()
    if not value or value.lower() == "all":
        return None

    issues = [x.strip() for x in value.split(",") if x.strip()]
    if not issues:
        return None
    return ",".join(issues)


def _expand_ordered_values(
    values: list[str],
    expected_count: int,
    *,
    label: str,
) -> list[str | None]:
    """将可选参数扩展为与模型数量等长的序列。"""
    normalized = [x.strip() for x in values if x.strip()]
    if not normalized:
        return [None] * expected_count
    if len(normalized) == 1:
        return normalized * expected_count
    if len(normalized) == expected_count:
        return normalized
    raise ValueError(
        f"{label} 需要传 1 个（应用到全部模型）或 {expected_count} 个（与模型一一对应），"
        f"当前为 {len(normalized)} 个。"
    )


def _expand_ordered_profiles(
    profiles: list[dict[str, object]],
    expected_count: int,
) -> list[dict[str, object]]:
    """将 profile 列表扩展为与模型数量一致。"""
    if not profiles:
        return [{} for _ in range(expected_count)]
    if len(profiles) == 1:
        return [dict(profiles[0]) for _ in range(expected_count)]
    if len(profiles) == expected_count:
        return [dict(p) for p in profiles]
    raise ValueError(
        "--thinking-profile/--thinking-profiles-json "
        f"需要传 1 个或 {expected_count} 个 profile，当前为 {len(profiles)} 个。"
    )


def _deep_merge_dict(dst: dict[str, object], src: dict[str, object]) -> None:
    """递归合并字典。"""
    for key, value in src.items():
        if key in dst and isinstance(dst[key], dict) and isinstance(value, dict):
            _deep_merge_dict(dst[key], value)
        else:
            dst[key] = value


def _parse_json_object(raw: str, *, label: str) -> dict[str, object]:
    """解析 JSON 对象字符串。"""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} 必须是 JSON 对象。")
    return parsed


def _coerce_bool(value: object, *, label: str) -> bool:
    """将 profile 中的布尔值标准化。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        mapping = {
            "1": True,
            "0": False,
            "true": True,
            "false": False,
            "yes": True,
            "no": False,
            "on": True,
            "off": False,
        }
        if text in mapping:
            return mapping[text]
    raise ValueError(f"{label} 需要布尔值（true/false）。")


def _normalize_profile_key(raw_key: str) -> str:
    """兼容 snake_case / camelCase / kebab-case 的 profile key。"""
    key = raw_key.strip().replace("-", "_")
    aliases = {
        "reasoningEffort": "reasoning_effort",
        "thinkingLevel": "thinking_level",
        "thinkingType": "thinking_type",
        "outputEffort": "output_effort",
        "thinkingBudget": "thinking_budget",
        "enableThinking": "enable_thinking",
        "budgetTokens": "budget_tokens",
        "reasoningJson": "reasoning_json",
        "extraBody": "extra_body",
        "extraBodyJson": "extra_body_json",
    }
    return aliases.get(key, key)


def _as_json_object(value: object, *, label: str) -> dict[str, object]:
    """接受 dict 或 JSON 字符串并转为 dict。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return _parse_json_object(value, label=label)
    raise ValueError(f"{label} 需要 JSON 对象或 JSON 字符串。")


def _profile_to_run_args(profile: dict[str, object]) -> list[str]:
    """将单模型 profile 转换为 run.py CLI 参数。"""
    scalar_args: list[str] = []
    reasoning_obj: dict[str, object] = {}
    extra_body_obj: dict[str, object] = {}

    for raw_key, value in profile.items():
        key = _normalize_profile_key(raw_key)

        if key == "effort":
            effort = str(value).strip().lower()
            if effort not in _EFFORT_CHOICES:
                raise ValueError(f"profile.effort 不合法：{effort}")
            scalar_args.extend(["--effort", effort])
            continue

        if key == "reasoning_effort":
            scalar_args.extend(["--reasoning-effort", str(value).strip().lower()])
            continue
        if key == "thinking_level":
            scalar_args.extend(["--thinking-level", str(value).strip().lower()])
            continue
        if key == "thinking_type":
            scalar_args.extend(["--thinking-type", str(value).strip().lower()])
            continue
        if key == "output_effort":
            scalar_args.extend(["--output-effort", str(value).strip().lower()])
            continue
        if key == "thinking_budget":
            scalar_args.extend(["--thinking-budget", str(value)])
            continue
        if key == "budget_tokens":
            scalar_args.extend(["--budget-tokens", str(value)])
            continue
        if key == "enable_thinking":
            enabled = _coerce_bool(value, label="profile.enable_thinking")
            scalar_args.extend(["--enable-thinking", "true" if enabled else "false"])
            continue

        if key in {"reasoning", "reasoning_json"}:
            _deep_merge_dict(
                reasoning_obj,
                _as_json_object(value, label=f"profile.{raw_key}"),
            )
            continue

        if key in {"extra_body", "extra_body_json"}:
            _deep_merge_dict(
                extra_body_obj,
                _as_json_object(value, label=f"profile.{raw_key}"),
            )
            continue

        # 未识别字段默认透传到 extra_body，便于覆盖供应商私有参数。
        extra_body_obj[key] = value

    if reasoning_obj:
        scalar_args.extend(
            ["--reasoning-json", json.dumps(reasoning_obj, ensure_ascii=False)]
        )
    if extra_body_obj:
        scalar_args.extend(
            ["--extra-body-json", json.dumps(extra_body_obj, ensure_ascii=False)]
        )

    return scalar_args


def _build_cmd(
    model: str,
    issue_arg: str | None,
    force: bool,
    *,
    ordered_effort: str | None,
    profile_args: list[str],
    retest: bool,
    retest_force: bool,
    retest_dir: str,
) -> list[str]:
    """构造 run.py 命令。"""
    cmd = [sys.executable, str(_RUN_PATH), "--model", model]
    if issue_arg is not None:
        cmd.append(issue_arg)
    if ordered_effort is not None:
        cmd.extend(["--effort", ordered_effort])
    cmd.extend(profile_args)
    if force:
        cmd.append("-f")
    if retest:
        cmd.append("--retest")
    if retest_force:
        cmd.append("--retest-force")
    if retest_dir.strip():
        cmd.extend(["--retest-dir", retest_dir.strip()])
    return cmd


def _split_csv_values(part: str) -> list[str]:
    """拆分逗号分隔值并去掉空白。"""
    return [x.strip() for x in part.split(",") if x.strip()]


def _parse_efforts(effort_args: list[str], efforts_csv: str) -> list[str]:
    """解析 --effort / --efforts。"""
    values: list[str] = []
    for item in effort_args:
        values.extend(_split_csv_values(item))
    if efforts_csv.strip():
        values.extend(_split_csv_values(efforts_csv))
    return [x.lower() for x in values]


def _parse_profile_json_list(
    profile_args: list[str],
    profiles_json: str,
) -> list[dict[str, object]]:
    """解析 --thinking-profile / --thinking-profiles-json。"""
    raw_items = [x.strip() for x in profile_args if x.strip()]

    if profiles_json.strip():
        parsed = json.loads(profiles_json)
        if isinstance(parsed, dict):
            raw_items.append(json.dumps(parsed, ensure_ascii=False))
        elif isinstance(parsed, list):
            for idx, item in enumerate(parsed):
                if not isinstance(item, dict):
                    raise ValueError(f"--thinking-profiles-json 第 {idx} 项不是对象。")
                raw_items.append(json.dumps(item, ensure_ascii=False))
        else:
            raise ValueError("--thinking-profiles-json 必须是 JSON 对象或对象数组。")

    profiles: list[dict[str, object]] = []
    for idx, raw in enumerate(raw_items):
        profiles.append(_parse_json_object(raw, label=f"thinking profile #{idx + 1}"))
    return profiles


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "批量执行 agent/run.py：按顺序指定模型，并可按顺序指定思考强度/参数。"
        )
    )
    parser.add_argument(
        "-m",
        "--model",
        dest="model_args",
        action="append",
        default=[],
        help="模型名（可重复传入，按顺序执行）。例：--model openai/gpt-5.3-codex --model qwen/qwen3.5-plus",
    )
    parser.add_argument(
        "--models",
        default="",
        help="逗号分隔模型列表。例：--models openai/gpt-5.3-codex,qwen/qwen3.5-plus",
    )
    parser.add_argument(
        "--issues",
        default="all",
        help="测试用例：all（默认）/单个 issue/逗号分隔多个 issue。例：104772,85250",
    )

    # 按模型顺序传通用思考强度：1个=应用到全部模型；N个=与模型一一对应
    parser.add_argument(
        "--effort",
        dest="effort_args",
        action="append",
        default=[],
        help=(
            "通用思考强度（none/minimal/low/medium/high/xhigh/max）。"
            "可重复传入，按模型顺序对应。"
        ),
    )
    parser.add_argument(
        "--efforts",
        default="",
        help="逗号分隔的通用思考强度列表（按模型顺序对应）。",
    )

    # 按模型顺序传完整 profile（JSON 对象）
    parser.add_argument(
        "--thinking-profile",
        dest="thinking_profile_args",
        action="append",
        default=[],
        help=(
            "单模型思考参数 JSON。支持 effort/reasoning_effort/thinking_level/"
            "thinking_type/output_effort/enable_thinking/thinking_budget/budget_tokens/"
            "reasoning/extra_body 等键。"
        ),
    )
    parser.add_argument(
        "--thinking-profiles-json",
        default="",
        help="JSON 对象或对象数组，按模型顺序传思考参数。",
    )

    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="透传 -f 给 agent/run.py，覆盖已有结果。",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="任一模型执行失败时立即停止（默认继续执行后续模型）。",
    )
    parser.add_argument(
        "--model-timeout-seconds",
        type=int,
        default=0,
        help=(
            "单模型运行超时秒数（0 表示不设超时）。"
            "超时返回码按 124 记录，并继续后续模型（除非 --stop-on-error）。"
        ),
    )
    parser.add_argument(
        "--retest",
        "--retest-patches",
        dest="retest_patches",
        action="store_true",
        help="每轮 run.py 结束后直接调用 agent/retest_patches.py 复测同批 patch。",
    )
    parser.add_argument(
        "--retest-force",
        action="store_true",
        help="透传 --retest-force 给 run.py，覆盖已有复测结果。",
    )
    parser.add_argument(
        "--retest-dir",
        default="",
        help=(
            "指定复测输出目录。"
            "单模型时直接透传；多模型时自动追加 /<safe_model> 子目录避免互相覆盖。"
        ),
    )

    args = parser.parse_args()

    if args.retest_force or args.retest_dir.strip():
        args.retest_patches = True

    models = _extract_models_in_cli_order(sys.argv[1:])
    if not models:
        models = _parse_models(args.model_args, args.models)
    if not models:
        parser.error("请至少通过 --model 或 --models 指定一个模型。")

    raw_efforts = _parse_efforts(args.effort_args, args.efforts)
    invalid_efforts = [x for x in raw_efforts if x not in _EFFORT_CHOICES]
    if invalid_efforts:
        parser.error(
            "--effort/--efforts 存在非法值："
            + ", ".join(invalid_efforts)
            + f"。可选值：{', '.join(_EFFORT_CHOICES)}"
        )

    try:
        ordered_efforts = _expand_ordered_values(
            raw_efforts,
            len(models),
            label="--effort/--efforts",
        )
    except ValueError as exc:
        parser.error(str(exc))

    try:
        raw_profiles = _parse_profile_json_list(
            args.thinking_profile_args,
            args.thinking_profiles_json,
        )
        ordered_profiles = _expand_ordered_profiles(raw_profiles, len(models))
    except (ValueError, json.JSONDecodeError) as exc:
        parser.error(f"思考参数解析失败：{exc}")

    if not _ENV_PATH.exists():
        print(
            f"[WARN] 未找到 {_ENV_PATH}。若未通过系统环境变量提供密钥，"
            "run.py 可能因缺少 LLM 配置而失败。"
        )
    if not _RUN_PATH.exists():
        parser.error(f"未找到 {_RUN_PATH}。")

    issue_arg = _normalize_issue_arg(args.issues)

    print("=" * 72)
    print("Batch agent test runner")
    print(f"Models ({len(models)}): {models}")
    print(f"Issues: {'all' if issue_arg is None else issue_arg}")
    print(f"Ordered efforts: {ordered_efforts}")
    print(f"Thinking profiles count: {len(raw_profiles)}")
    print(f"Force overwrite: {args.force}")
    print(
        "Per-model timeout (seconds): "
        + (
            str(args.model_timeout_seconds)
            if args.model_timeout_seconds > 0
            else "disabled"
        )
    )
    print(f"Retest: {args.retest_patches}")
    if args.retest_patches:
        print(
            "Retest dir: "
            + (
                args.retest_dir.strip()
                if args.retest_dir.strip()
                else "(run.py default)"
            )
        )
        print(f"Retest force overwrite: {args.retest_force}")
    print("=" * 72)

    summary: list[tuple[str, int]] = []

    for idx, model in enumerate(models, start=1):
        merged_profile: dict[str, object] = {}
        effort = ordered_efforts[idx - 1]
        if effort is not None:
            merged_profile["effort"] = effort
        _deep_merge_dict(merged_profile, ordered_profiles[idx - 1])

        try:
            per_model_profile_args = _profile_to_run_args(merged_profile)
        except ValueError as exc:
            print(f"[ERROR] model={model} profile invalid: {exc}")
            summary.append((model, 2))
            if args.stop_on_error:
                break
            continue

        print(f"\n[{idx}/{len(models)}] Model={model}")
        if merged_profile:
            print("  thinking profile:")
            print(json.dumps(merged_profile, indent=2, ensure_ascii=False))
        else:
            print("  thinking profile: {} (use run.py defaults)")

        per_model_retest_dir = ""
        if args.retest_dir.strip():
            if len(models) == 1:
                per_model_retest_dir = args.retest_dir.strip()
            else:
                per_model_retest_dir = str(
                    Path(args.retest_dir.strip()) / _safe_path_part(model)
                )

        cmd = _build_cmd(
            model,
            issue_arg,
            args.force,
            ordered_effort=None,
            profile_args=per_model_profile_args,
            retest=args.retest_patches,
            retest_force=args.retest_force,
            retest_dir=per_model_retest_dir,
        )
        cmd_display = " ".join(shlex.quote(x) for x in cmd)
        print(f"Run: {cmd_display}")

        run_timeout = (
            args.model_timeout_seconds if args.model_timeout_seconds > 0 else None
        )
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(_PROJECT_ROOT),
                check=False,
                timeout=run_timeout,
            )
            return_code = proc.returncode
        except subprocess.TimeoutExpired:
            return_code = 124
            print(
                f"[ERROR] model={model} timed out after {args.model_timeout_seconds}s"
            )

        summary.append((model, return_code))

        if return_code != 0:
            print(f"[ERROR] model={model}, returncode={return_code}")
            if args.stop_on_error:
                break
        else:
            print(f"[OK] model={model}")

    print("\n" + "=" * 72)
    print("Summary")
    for model, code in summary:
        status = "OK" if code == 0 else f"FAIL({code})"
        print(f"- {model}: {status}")
    print("Model selection is passed via run.py --model (agent/.env is not modified).")
    print("=" * 72)

    has_error = any(code != 0 for _, code in summary)
    return 1 if has_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
