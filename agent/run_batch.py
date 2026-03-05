#!/usr/bin/env python3
"""agent/run_batch.py — 按模型与测试用例批量执行 agent/run.py。

需求对应：
1) 支持一个或多个模型，严格按传入顺序执行；
2) 支持单个/多个/全部测试用例（issue），并对所有模型生效；
3) 模型切换通过修改 agent/.env 中 LAB_LLM_MODEL 实现，再启动 run.py。
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_ENV_PATH = _THIS_DIR / ".env"
_RUN_PATH = _THIS_DIR / "run.py"
_MODEL_ENV_KEY = "LAB_LLM_MODEL"


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


def _set_env_value(env_path: Path, key: str, value: str) -> None:
    """更新 .env 中指定 key 的值；若不存在则追加。"""
    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    replaced = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        k, sep, _ = stripped.partition("=")
        if sep and k.strip() == key:
            indent = line[: len(line) - len(line.lstrip())]
            lines[idx] = f"{indent}{key}={value}\n"
            replaced = True
            break

    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")

    env_path.write_text("".join(lines), encoding="utf-8")


def _build_cmd(issue_arg: str | None, force: bool) -> list[str]:
    """构造 run.py 命令。"""
    cmd = [sys.executable, str(_RUN_PATH)]
    if issue_arg is not None:
        cmd.append(issue_arg)
    if force:
        cmd.append("-f")
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "批量执行 agent 测试：先改 agent/.env 的 LAB_LLM_MODEL，再运行 agent/run.py。"
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

    args = parser.parse_args()

    models = _extract_models_in_cli_order(sys.argv[1:])
    if not models:
        models = _parse_models(args.model_args, args.models)
    if not models:
        parser.error("请至少通过 --model 或 --models 指定一个模型。")

    if not _ENV_PATH.exists():
        parser.error(f"未找到 {_ENV_PATH}，请先从 .env.example 复制并填写 .env。")
    if not _RUN_PATH.exists():
        parser.error(f"未找到 {_RUN_PATH}。")

    issue_arg = _normalize_issue_arg(args.issues)

    print("=" * 72)
    print("Batch agent test runner")
    print(f"Models ({len(models)}): {models}")
    print(f"Issues: {'all' if issue_arg is None else issue_arg}")
    print(f"Force overwrite: {args.force}")
    print("=" * 72)

    summary: list[tuple[str, int]] = []

    for idx, model in enumerate(models, start=1):
        print(f"\n[{idx}/{len(models)}] Set {_MODEL_ENV_KEY}={model}")
        _set_env_value(_ENV_PATH, _MODEL_ENV_KEY, model)

        cmd = _build_cmd(issue_arg, args.force)
        cmd_display = " ".join(shlex.quote(x) for x in cmd)
        print(f"Run: {cmd_display}")

        proc = subprocess.run(cmd, cwd=str(_PROJECT_ROOT), check=False)
        summary.append((model, proc.returncode))

        if proc.returncode != 0:
            print(f"[ERROR] model={model}, returncode={proc.returncode}")
            if args.stop_on_error:
                break
        else:
            print(f"[OK] model={model}")

    print("\n" + "=" * 72)
    print("Summary")
    for model, code in summary:
        status = "OK" if code == 0 else f"FAIL({code})"
        print(f"- {model}: {status}")
    print(f"Current .env {_MODEL_ENV_KEY}: {summary[-1][0] if summary else 'N/A'}")
    print("=" * 72)

    has_error = any(code != 0 for _, code in summary)
    return 1 if has_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
