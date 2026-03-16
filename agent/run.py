#!/usr/bin/env python3
"""agent/run.py — 基于 mini-swe-agent 的 LLVM InstCombine 修复主编排脚本。

用法:
    python agent/run.py                     # 处理 dataset/ 下全部 issue
    python agent/run.py 85250               # 处理指定 issue
    python agent/run.py 85250 -f            # 覆盖已有结果
    python agent/run.py 85250 --fresh-run   # 每次从全新工作区和全新 build 开始
    python agent/run.py 85250,76128         # 处理多个 issue（逗号分隔）
    python agent/run.py 85250 --retest      # 结束后直接调用 agent/retest_patches.py 复测同批 patch
    python agent/run.py 85250 --retest --retest-force

输出:
    results/agent/<safe_model>/<safe_issue>/<timestamp>/fix.json
    results/agent/<safe_model>/<safe_issue>/<timestamp>/traj.json
    results/agent/<safe_model>/<safe_issue>/<timestamp>/preds.json

可选复测输出（--retest）:
    results/agent/<safe_model>/<safe_issue>/<timestamp>/retest.json

环境变量配置:
    cp agent/.env.example agent/.env   # 复制模板并填写，即可完全替代 init_agent_env.sh
    详见 env_config.py（优先级：系统环境变量 > .env > 代码默认值；dataset 固定为仓库 dataset/）
"""

import argparse
import os
import sys
import json
import time
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

# 设置 sys.path
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
_PROJECT_DATASET_DIR = os.path.join(_PROJECT_ROOT, "dataset")

# 与 pipeline 保持一致：强制仅使用仓库内 dataset/。
# 需要在导入 env_config / llvm_helper 之前设置，避免读取到旧数据集路径。
os.environ["LAB_DATASET_DIR"] = _PROJECT_DATASET_DIR

sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))
sys.path.insert(0, _THIS_DIR)

# 先加载集中配置（会自动读取 agent/.env）并补齐关键环境变量，
# 避免 scripts/llvm_helper.py 在 import 时读取不到 LAB_LLVM_DIR。
from env_config import cfg

# 双保险：即便外部 shell/.env 注入了其它值，也仅使用 dataset/。
cfg.dataset_dir = _PROJECT_DATASET_DIR


def _ensure_writable_tmp_dir(path: str) -> str:
    """确保临时目录存在且可写；失败时抛异常以便上层快速失败。"""
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

import llvm_helper  # pyright: ignore[reportMissingImports]
from lab_env import Environment as Env  # pyright: ignore[reportMissingImports]

# mini-swe-agent imports
from minisweagent.models import get_model
from llvm_env import LLVMEnvironment
from llvm_agent import LLVMFixAgent

# ──────────────────────────────────────────────────────────────────────
# 由 env_config.cfg 集中管理的配置（见 agent/env_config.py）
# ──────────────────────────────────────────────────────────────────────

RESULTS_AGENT_DIR = os.path.join(_PROJECT_ROOT, "results", "agent")
os.makedirs(RESULTS_AGENT_DIR, exist_ok=True)

UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")


def _safe_env_int(key: str, default: int) -> int:
    """Read integer env var with safe fallback."""
    try:
        return int(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def _safe_env_bool(key: str, default: bool) -> bool:
    """Read boolean env var with safe fallback."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


LOCALIZE_MODE_PIPELINE = "pipeline"
LOCALIZE_MODE_LITE = "lite"
LOCALIZE_MODE_CHOICES = (LOCALIZE_MODE_PIPELINE, LOCALIZE_MODE_LITE)
LOCALIZE_MODE_ENV_KEY = "LAB_AGENT_LOCALIZE_MODE"
LOCALIZE_DEFAULT_MODE = LOCALIZE_MODE_PIPELINE

LOCALIZE_RUNTIME_ENABLED = os.environ.get("LAB_AGENT_RUNTIME_LOCALIZE", "ON") == "ON"
LOCALIZE_TOP_K_FILES = max(1, _safe_env_int("LAB_AGENT_LOCALIZE_TOP_K_FILES", 3))
LOCALIZE_TOP_K_FUNCS = max(1, _safe_env_int("LAB_AGENT_LOCALIZE_TOP_K_FUNCS", 3))
LOCALIZE_MAX_FILE_CHARS = max(
    2000, _safe_env_int("LAB_AGENT_LOCALIZE_MAX_FILE_CHARS", 120000)
)
LOCALIZE_MAX_ISSUE_DESC_CHARS = max(
    2000, _safe_env_int("LAB_AGENT_LOCALIZE_MAX_ISSUE_DESC_CHARS", 20000)
)
LOCALIZE_MAX_DEBUG_CHARS = max(
    1000, _safe_env_int("LAB_AGENT_LOCALIZE_MAX_DEBUG_CHARS", 5000)
)
LOCALIZE_ENABLE_OPT_DEBUG = _safe_env_bool("LAB_AGENT_LOCALIZE_ENABLE_OPT_DEBUG", True)
LOCALIZE_BUILD_BEFORE_OPT_DEBUG = _safe_env_bool(
    "LAB_AGENT_LOCALIZE_BUILD_BEFORE_OPT_DEBUG", True
)
LOCALIZE_OPT_DEBUG_TIMEOUT = max(
    5, _safe_env_int("LAB_AGENT_LOCALIZE_OPT_DEBUG_TIMEOUT", 30)
)
LOCALIZE_TIMEOUT_SECONDS = max(15, _safe_env_int("LAB_AGENT_LOCALIZE_TIMEOUT", 120))
LIMITS_RETRY_ENABLED = os.environ.get("LAB_AGENT_LIMITS_RETRY", "ON") == "ON"
LIMITS_RETRY_EXTRA_STEPS = max(
    5, _safe_env_int("LAB_AGENT_LIMITS_RETRY_EXTRA_STEPS", 25)
)

LOCALIZE_CANDIDATE_FILES = [
    "llvm/lib/Transforms/InstCombine/InstCombineCalls.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineLoadStoreAlloca.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineSelect.cpp",
    "llvm/lib/Transforms/InstCombine/InstructionCombining.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineAddSub.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineCasts.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineMulDivRem.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineShifts.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineCompares.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineNegator.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineSimplifyDemanded.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineAtomicRMW.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombinePHI.cpp",
    "llvm/lib/Transforms/InstCombine/InstCombineVectorOps.cpp",
]

LOCALIZE_SYS_PROMPT_FILE = (
    "You are an LLVM maintainer. Users reported a missed optimization. "
    "Identify which InstCombine source file is most likely responsible."
)
LOCALIZE_SYS_PROMPT_FUNC = (
    "You are an LLVM maintainer. Users reported a missed optimization. "
    "Identify which function in the given file is most likely responsible."
)
LOCALIZE_TEXT_BLOCK_PATTERN = re.compile(r"```text(.*?)```", re.DOTALL)
LOCALIZE_FORMAT_ISSUE_DESC_FROM_PROGRAMS = """
The following program reveals the missed optimization.
The source program is
```llvm
{source_program}
```

However, current LLVM cannot optimize the source program. The expected optimized program is
```llvm
{expect_optimized_program}
```
"""
LOCALIZE_CACHE_FILENAME = "localization.json"


def _safe_path_part(value: str) -> str:
    """将字符串转换为可用于目录名的安全片段。"""
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value)


def _model_results_root() -> str:
    """当前模型在 results/agent 下的输出根目录。"""
    model_part = _safe_path_part(cfg.llm_model)
    model_root = os.path.join(RESULTS_AGENT_DIR, model_part)
    os.makedirs(model_root, exist_ok=True)
    return model_root


def _issue_results_dir(issue_id: str) -> str:
    """当前模型下单 issue 的稳定输出目录。"""
    issue_part = _safe_path_part(issue_id)
    issue_dir = os.path.join(_model_results_root(), issue_part)
    os.makedirs(issue_dir, exist_ok=True)
    return issue_dir


def _build_output_paths(issue_id: str) -> tuple[str, str, str, str]:
    """构造单 issue 的输出路径。

    目录结构:
      results/agent/<safe_model>/<safe_issue>/<timestamp>/
        - fix.json
        - traj.json
        - preds.json
    """
    issue_dir = _issue_results_dir(issue_id)

    run_dir = os.path.join(issue_dir, cfg.run_timestamp)
    os.makedirs(run_dir, exist_ok=True)

    fix_log_path = os.path.join(run_dir, "fix.json")
    traj_path = os.path.join(run_dir, "traj.json")
    preds_path = os.path.join(run_dir, "preds.json")
    return run_dir, fix_log_path, traj_path, preds_path


def _list_dataset_issue_ids() -> list[str]:
    """仅枚举 dataset/ 下的 issue JSON（不混入其它文件）。"""
    issue_ids: list[str] = []
    for name in sorted(os.listdir(llvm_helper.dataset_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(llvm_helper.dataset_dir, name)
        if not os.path.isfile(path):
            continue
        issue_ids.append(name[: -len(".json")])
    return issue_ids


_REASONING_EFFORT_CHOICES = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


def _parse_bool_arg(raw: str) -> bool:
    """解析 true/false 风格布尔参数。"""
    value = raw.strip().lower()
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
    if value not in mapping:
        raise argparse.ArgumentTypeError("布尔参数仅支持: true/false/1/0/yes/no/on/off")
    return mapping[value]


def _parse_json_object_arg(raw: str, arg_name: str) -> dict[str, object]:
    """解析 JSON 对象参数。"""
    text = raw.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{arg_name} 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{arg_name} 必须是 JSON 对象。")
    return parsed


def _deep_merge_dict(dst: dict[str, object], src: dict[str, object]) -> None:
    """递归合并字典（src 覆盖 dst）。"""
    for key, value in src.items():
        if key in dst and isinstance(dst[key], dict) and isinstance(value, dict):
            _deep_merge_dict(dst[key], value)
        else:
            dst[key] = value


def _ensure_nested_dict(
    parent: dict[str, object],
    key: str,
    *,
    arg_name: str,
) -> dict[str, object]:
    """确保 parent[key] 为 dict，若缺失则创建。"""
    current = parent.get(key)
    if current is None:
        child: dict[str, object] = {}
    elif isinstance(current, dict):
        child = dict(current)
    else:
        raise ValueError(
            f"{arg_name} 与 extra_body.{key} 冲突：期望对象类型，"
            f"实际为 {type(current).__name__}。"
        )
    parent[key] = child
    return child


def _model_family(raw_model_name: str) -> str:
    """按已知模型命名规则分类，供参数映射与校验使用。"""
    model = raw_model_name.lower()

    if "gpt-5.3-codex" in model or "gpt-5.2-codex" in model:
        return "openai_codex"
    if "gpt-5-mini" in model:
        return "openai_gpt5_mini"
    if "gpt-5.4" in model or "gpt-5.2" in model:
        return "openai_gpt5_main"
    if "gpt-5" in model:
        return "openai_gpt5_generic"

    if "gemini-3.1-flash-lite-preview" in model:
        return "gemini_flash_lite"
    if "gemini-3.1-pro-preview" in model or "customtools" in model:
        return "gemini_pro"
    if "gemini" in model:
        return "gemini_generic"

    if "deepseek-reasoner" in model:
        return "deepseek_reasoner"
    if "deepseek-chat" in model:
        return "deepseek_chat"
    if "deepseek" in model:
        return "deepseek_generic"

    if "claude-sonnet-4.6" in model:
        return "claude_sonnet_46"
    if "claude-opus-4.6" in model:
        return "claude_opus_46"
    if "claude" in model:
        return "claude_generic"

    if "qwen3.5-plus" in model:
        return "qwen_35_plus"
    if "qwen" in model:
        return "qwen_generic"

    return "generic"


def _allowed_efforts_for_family(family: str) -> set[str]:
    """通用 --effort 在不同模型族上的可接受值。"""
    if family == "openai_codex":
        return {"minimal", "low", "medium", "high", "xhigh"}
    if family == "openai_gpt5_mini":
        return {"minimal", "low", "medium", "high"}
    if family == "openai_gpt5_main":
        return {"none", "low", "medium", "high", "xhigh"}

    if family in {"gemini_pro", "gemini_generic"}:
        # 文档不推荐 minimal，但网关验证可通过；仍保留以支持实验配置。
        return {"minimal", "low", "medium", "high"}
    if family == "gemini_flash_lite":
        return {"minimal", "low", "medium", "high"}

    if family in {"claude_sonnet_46", "claude_opus_46", "claude_generic"}:
        return {"low", "medium", "high", "max"}

    if family == "deepseek_reasoner":
        return {"minimal", "low", "medium", "high", "xhigh", "max"}

    return set(_REASONING_EFFORT_CHOICES)


def _apply_generic_effort(
    effort: str,
    family: str,
    reasoning: dict[str, object],
    extra_body: dict[str, object],
) -> None:
    """将通用 effort 映射为模型特定参数。"""
    if family.startswith("openai_"):
        reasoning["effort"] = effort
        return

    if family.startswith("gemini_"):
        extra_body["thinkingLevel"] = effort
        return

    if family.startswith("claude_"):
        thinking = _ensure_nested_dict(extra_body, "thinking", arg_name="--effort")
        thinking.setdefault("type", "adaptive")
        output_cfg = _ensure_nested_dict(
            extra_body,
            "output_config",
            arg_name="--effort",
        )
        output_cfg["effort"] = effort
        return

    if family == "deepseek_chat":
        thinking = _ensure_nested_dict(extra_body, "thinking", arg_name="--effort")
        thinking["type"] = "disabled" if effort == "none" else "enabled"
        return

    if family == "deepseek_reasoner":
        if effort == "none":
            raise ValueError(
                "deepseek-reasoner 不支持 --effort none（该模型默认推理模式）。"
            )
        thinking = _ensure_nested_dict(extra_body, "thinking", arg_name="--effort")
        thinking.setdefault("type", "enabled")
        return

    if family.startswith("qwen_"):
        if effort == "none":
            extra_body["enable_thinking"] = False
            return
        budget_by_effort = {
            "minimal": 1024,
            "low": 2048,
            "medium": 4096,
            "high": 8192,
            "xhigh": 16384,
            "max": 32768,
        }
        extra_body["enable_thinking"] = True
        extra_body.setdefault("thinking_budget", budget_by_effort.get(effort, 4096))
        return

    # 未识别模型：退化为 OpenAI 风格，便于透传实验。
    reasoning["effort"] = effort


def _build_model_thinking_overrides(
    args: argparse.Namespace,
    raw_model_name: str,
) -> dict[str, object]:
    """根据 CLI 选项构建模型调用时的 thinking/reasoning 参数。"""
    family = _model_family(raw_model_name)

    reasoning = _parse_json_object_arg(args.reasoning_json or "", "--reasoning-json")
    extra_body = _parse_json_object_arg(args.extra_body_json or "", "--extra-body-json")

    if args.effort:
        allowed_efforts = _allowed_efforts_for_family(family)
        if args.effort not in allowed_efforts:
            allowed_str = ", ".join(sorted(allowed_efforts))
            raise ValueError(
                f"模型 {raw_model_name} 不支持 --effort={args.effort}。"
                f"可选值：{allowed_str}"
            )
        _apply_generic_effort(args.effort, family, reasoning, extra_body)

    if args.reasoning_effort:
        if not family.startswith("openai_"):
            raise ValueError("--reasoning-effort 仅适用于 OpenAI GPT-5 系列模型。")
        reasoning["effort"] = args.reasoning_effort

    if args.thinking_level:
        if not family.startswith("gemini_"):
            raise ValueError("--thinking-level 仅适用于 Gemini 系列模型。")
        extra_body["thinkingLevel"] = args.thinking_level

    if args.thinking_type:
        if not (family.startswith("deepseek_") or family.startswith("claude_")):
            raise ValueError("--thinking-type 仅适用于 DeepSeek/Claude 系列模型。")
        thinking = _ensure_nested_dict(
            extra_body, "thinking", arg_name="--thinking-type"
        )
        thinking["type"] = args.thinking_type

    if args.output_effort:
        if not family.startswith("claude_"):
            raise ValueError("--output-effort 仅适用于 Claude 系列模型。")
        output_cfg = _ensure_nested_dict(
            extra_body,
            "output_config",
            arg_name="--output-effort",
        )
        output_cfg["effort"] = args.output_effort

    if args.enable_thinking is not None:
        if not family.startswith("qwen_"):
            raise ValueError("--enable-thinking 仅适用于 Qwen 系列模型。")
        extra_body["enable_thinking"] = args.enable_thinking

    if args.thinking_budget is not None:
        if args.thinking_budget <= 0:
            raise ValueError("--thinking-budget 必须为正整数。")
        if not (family.startswith("gemini_") or family.startswith("qwen_")):
            raise ValueError("--thinking-budget 仅适用于 Gemini/Qwen 系列模型。")
        if family == "qwen_35_plus" and args.thinking_budget > 81920:
            raise ValueError("qwen3.5-plus 的 thinking_budget 最大为 81920。")
        extra_body["thinking_budget"] = args.thinking_budget

    if args.budget_tokens is not None:
        if args.budget_tokens <= 0:
            raise ValueError("--budget-tokens 必须为正整数。")
        if not family.startswith("claude_"):
            raise ValueError("--budget-tokens 仅适用于 Claude 系列模型。")
        thinking = _ensure_nested_dict(
            extra_body, "thinking", arg_name="--budget-tokens"
        )
        thinking["budget_tokens"] = args.budget_tokens

    # OpenAI 不同模型的 effort 枚举不同，做额外校验避免误配。
    effort_value = reasoning.get("effort")
    if isinstance(effort_value, str):
        if family == "openai_codex":
            allowed_reasoning = {"minimal", "low", "medium", "high", "xhigh"}
        elif family == "openai_gpt5_main":
            allowed_reasoning = {"none", "low", "medium", "high", "xhigh"}
        elif family == "openai_gpt5_mini":
            allowed_reasoning = {"minimal", "low", "medium", "high"}
        else:
            allowed_reasoning = set()

        if allowed_reasoning and effort_value not in allowed_reasoning:
            allowed_str = ", ".join(sorted(allowed_reasoning))
            raise ValueError(
                f"模型 {raw_model_name} 不支持 reasoning.effort={effort_value}。"
                f"可选值：{allowed_str}"
            )
    elif "effort" in reasoning:
        raise ValueError("reasoning.effort 必须为字符串。")

    if family.startswith("claude_"):
        thinking_obj = extra_body.get("thinking")
        if isinstance(thinking_obj, dict):
            if (
                thinking_obj.get("type") == "enabled"
                and "budget_tokens" not in thinking_obj
            ):
                raise ValueError(
                    "Claude 模型使用 thinking.type=enabled 时必须同时提供 --budget-tokens。"
                )
        if family == "claude_sonnet_46":
            output_cfg = extra_body.get("output_config")
            if isinstance(output_cfg, dict) and output_cfg.get("effort") == "max":
                print(
                    "[WARN] claude-sonnet-4.6 文档标注不支持 output_config.effort=max，"
                    "当前将按网关透传继续执行。"
                )

    if family == "deepseek_reasoner":
        thinking_obj = extra_body.get("thinking")
        if isinstance(thinking_obj, dict) and thinking_obj.get("type") == "disabled":
            raise ValueError("deepseek-reasoner 不支持 thinking.type=disabled。")

    overrides: dict[str, object] = {}
    if reasoning:
        overrides["reasoning"] = reasoning
    if extra_body:
        overrides["extra_body"] = extra_body
    return overrides


def _parse_cli_args(argv: list[str]) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="运行 agent 修复流程，并可选直接调用 agent/retest_patches.py 复测。"
    )
    parser.add_argument(
        "issues",
        nargs="?",
        default="all",
        help="issue 列表：all（默认）/单个 issue/逗号分隔多个 issue",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="覆盖已有修复结果。",
    )
    parser.add_argument(
        "--fresh-run",
        action="store_true",
        help=(
            "每个 issue 运行前清理并重建工作区：删除 utils/llvm-<issue>、"
            "build/<issue> 与 build/<issue>_cache。"
        ),
    )
    parser.add_argument(
        "--retest",
        "--retest-patches",
        dest="retest_patches",
        action="store_true",
        help="运行结束后直接调用 agent/retest_patches.py 对同批 patch 复测。",
    )
    parser.add_argument(
        "--retest-force",
        action="store_true",
        help="透传 -f 给 agent/retest_patches.py，覆盖已有复测结果。",
    )
    parser.add_argument(
        "--retest-dir",
        default="",
        help=(
            "复测输出目录（传给 LAB_RETEST_DIR）。" "默认：results/agent/<safe_model>"
        ),
    )

    # LLM 参数覆盖（优先于 .env）
    parser.add_argument(
        "--model",
        default="",
        help="覆盖 LAB_LLM_MODEL（例如 openai/gpt-5.4、qwen/qwen3.5-plus）。",
    )
    parser.add_argument(
        "--localize-mode",
        choices=list(LOCALIZE_MODE_CHOICES),
        default=None,
        help=(
            "运行时 localization 模式（pipeline|lite）。"
            "优先级：CLI > LAB_AGENT_LOCALIZE_MODE > pipeline（默认）。"
        ),
    )

    # 通用强度档位（会根据模型自动映射到 reasoning/thinking 参数）
    parser.add_argument(
        "--effort",
        choices=list(_REASONING_EFFORT_CHOICES),
        default=None,
        help="通用思考强度档位（none/minimal/low/medium/high/xhigh/max），按模型自动映射。",
    )

    # OpenAI GPT-5 系列
    parser.add_argument(
        "--reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
        default=None,
        help="显式设置 OpenAI reasoning.effort。",
    )

    # Gemini 系列
    parser.add_argument(
        "--thinking-level",
        choices=["minimal", "low", "medium", "high"],
        default=None,
        help="显式设置 Gemini thinkingLevel。",
    )

    # DeepSeek / Claude 系列
    parser.add_argument(
        "--thinking-type",
        choices=["enabled", "disabled", "adaptive"],
        default=None,
        help="显式设置 thinking.type（DeepSeek/Claude）。",
    )

    # Claude 系列
    parser.add_argument(
        "--output-effort",
        choices=["low", "medium", "high", "max"],
        default=None,
        help="显式设置 Claude output_config.effort。",
    )
    parser.add_argument(
        "--budget-tokens",
        type=int,
        default=None,
        help="设置 Claude thinking.budget_tokens（配合 thinking.type=enabled）。",
    )

    # Qwen / Gemini
    parser.add_argument(
        "--thinking-budget",
        type=int,
        default=None,
        help="设置 thinking_budget（Gemini/Qwen）。",
    )

    # Qwen
    parser.add_argument(
        "--enable-thinking",
        type=_parse_bool_arg,
        default=None,
        metavar="true|false",
        help="设置 Qwen enable_thinking。",
    )

    # 兜底 JSON 透传
    parser.add_argument(
        "--reasoning-json",
        default="",
        help="直接透传 reasoning JSON 对象（会与其它参数合并）。",
    )
    parser.add_argument(
        "--extra-body-json",
        default="",
        help="直接透传 extra_body JSON 对象（会与其它参数合并）。",
    )

    return parser.parse_args(argv)


def _resolve_localize_mode(cli_mode: str | None) -> tuple[str, str]:
    """Resolve localization mode with priority: CLI > env > default."""
    if cli_mode:
        mode = cli_mode.strip().lower()
        source = "cli"
    else:
        raw_env = os.environ.get(LOCALIZE_MODE_ENV_KEY, "").strip().lower()
        if raw_env:
            mode = raw_env
            source = "env"
        else:
            mode = LOCALIZE_DEFAULT_MODE
            source = "default"

    if mode not in LOCALIZE_MODE_CHOICES:
        allowed = ", ".join(LOCALIZE_MODE_CHOICES)
        raise ValueError(
            f"{LOCALIZE_MODE_ENV_KEY}={mode!r} 无效（允许值：{allowed}）。"
        )
    return mode, source


def _default_retest_root() -> str:
    """构造 agent 默认复测输出根目录。"""
    return _model_results_root()


def _run_agent_retest(
    issue_ids: list[str] | None,
    *,
    override: bool,
    retest_root: str,
) -> int:
    """调用 agent/retest_patches.py 对当前模型目录下 patch 复测。"""
    retest_script = os.path.join(_THIS_DIR, "retest_patches.py")
    if not os.path.isfile(retest_script):
        print(f"[WARN] Skip retest: script not found: {retest_script}")
        return 0

    patch_source_dir = _model_results_root()

    if not os.path.isdir(patch_source_dir):
        print(f"[WARN] Skip retest: patch dir not found: {patch_source_dir}")
        return 0

    target_retest_root = retest_root.strip() or _default_retest_root()

    cmd = [sys.executable, retest_script]
    if issue_ids:
        cmd.append(",".join(issue_ids))
    if override:
        cmd.append("-f")

    env = os.environ.copy()
    env["LAB_PATCH_DIR"] = patch_source_dir
    env["LAB_RETEST_DIR"] = target_retest_root

    print("\n[Retest] Start agent/retest_patches.py")
    print(f"[Retest] LAB_PATCH_DIR={patch_source_dir}")
    print(f"[Retest] LAB_RETEST_DIR={target_retest_root}")
    print(f"[Retest] Command: {' '.join(cmd)}")

    proc = subprocess.run(cmd, cwd=_PROJECT_ROOT, env=env, check=False)
    if proc.returncode == 0:
        print("[Retest] Done")
    else:
        print(f"[Retest] Failed with return code {proc.returncode}")
    return proc.returncode


# ──────────────────────────────────────────────────────────────────────
# 辅助函数（复用自 pipeline/generate.py）
# ──────────────────────────────────────────────────────────────────────


def ensure_llvm_clone_for_issue(issue_id: str, base_llvm_dir: str = "") -> str:
    """为指定 issue 准备独立的 LLVM 克隆（复用 generate.py 逻辑）。"""
    issue_dir = os.path.join(UTILS_DIR, f"llvm-{issue_id}")
    if os.path.isdir(issue_dir):
        return issue_dir
    os.makedirs(UTILS_DIR, exist_ok=True)
    if base_llvm_dir and os.path.isdir(base_llvm_dir):
        print(f"Cloning llvm-project from {base_llvm_dir} to {issue_dir}")
        subprocess.check_call(["git", "clone", base_llvm_dir, issue_dir])
    else:
        print(f"Cloning llvm-project from remote to {issue_dir}")
        subprocess.check_call(
            ["git", "clone", "https://github.com/llvm/llvm-project.git", issue_dir]
        )
    return issue_dir


def _clean_issue_workspace(issue_id: str) -> None:
    """清理 issue 相关工作区，确保从全新 clone / build 开始。"""
    issue_dir = os.path.join(UTILS_DIR, f"llvm-{issue_id}")
    build_dir = os.path.join(cfg.llvm_build_dir, issue_id)
    build_cache_dir = os.path.join(cfg.llvm_build_dir, issue_id + "_cache")
    targets = [issue_dir, build_dir, build_cache_dir]

    for path in targets:
        if not os.path.exists(path):
            continue
        print(f"[FreshRun] Remove: {path}")
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n<Truncated>..."


def _get_first_missed_optimization_test_case(
    env: Env,
) -> tuple[str, str, str, dict] | None:
    """Return first mismatched test case: source/current/expected/test_file."""
    for test_file in env.get_tests():
        tests = test_file.get("tests", [])
        if not isinstance(tests, list):
            continue
        for test in tests:
            if not isinstance(test, dict):
                continue
            source_program = str(test.get("source_program", ""))
            cur = str(test.get("current_optimized_program", "")).strip()
            exp = str(test.get("expect_optimized_program", "")).strip()
            if cur != exp:
                return source_program, cur, exp, test_file
    return None


def _build_pipeline_issue_desc(
    source_program: str,
    current_optimized_program: str,
    expect_optimized_program: str,
) -> str:
    del current_optimized_program
    desc = LOCALIZE_FORMAT_ISSUE_DESC_FROM_PROGRAMS.format(
        source_program=source_program,
        expect_optimized_program=expect_optimized_program,
    )
    return _truncate_text(desc, LOCALIZE_MAX_ISSUE_DESC_CHARS)


def _first_missed_optimization_desc(env: Env) -> str:
    """提取首个未优化测试样例并构造轻量 issue 描述。"""
    case = _get_first_missed_optimization_test_case(env)
    if not case:
        return ""
    source_program, _cur, expect_optimized_program, _test_file = case
    return (
        "The following program reveals the missed optimization.\n"
        f"The source program is:\n```llvm\n{source_program}\n```\n\n"
        f"The expected optimized program is:\n```llvm\n{expect_optimized_program}\n```\n"
    )


def _normalize_str_list(raw: object, *, limit: int) -> list[str]:
    """Normalize list-like/string model output into deduplicated strings."""
    if isinstance(raw, str):
        values: object = [raw]
    else:
        values = raw
    if not isinstance(values, list):
        return []

    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        text = item.strip().strip("`").strip()
        if not text:
            continue
        if text not in normalized:
            normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _normalize_pred_funcs(raw: object) -> dict[str, list[str]]:
    """Normalize pred_funcs payload from localization JSON."""
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, list[str]] = {}
    for raw_file, raw_funcs in raw.items():
        if not isinstance(raw_file, str):
            continue
        file_key = raw_file.strip()
        if not file_key:
            continue
        funcs = _normalize_str_list(raw_funcs, limit=LOCALIZE_TOP_K_FUNCS)
        if funcs:
            normalized[file_key] = funcs
    return normalized


def _extract_text_block_lines(model_output: str) -> list[str]:
    m = LOCALIZE_TEXT_BLOCK_PATTERN.search(model_output or "")
    if not m:
        return []
    body = m.group(1).strip()
    return [ln.strip() for ln in body.splitlines() if ln.strip()]


def _extract_topk_lines_from_model_output(model_output: str, top_k: int) -> list[str]:
    """Extract ranked lines from model output (`text` block preferred)."""
    lines = _extract_text_block_lines(model_output)
    if not lines:
        lines = [ln.strip() for ln in (model_output or "").splitlines() if ln.strip()]

    cleaned: list[str] = []
    for line in lines:
        value = line.strip().strip("`").strip()
        value = value.lstrip("-* ").strip()
        if not value:
            continue
        if value not in cleaned:
            cleaned.append(value)
        if len(cleaned) >= top_k:
            break
    return cleaned


def _build_file_localize_prompt_lite(issue_desc: str) -> str:
    """Build lightweight file localization prompt."""
    candidate_lines = "\n".join(os.path.basename(x) for x in LOCALIZE_CANDIDATE_FILES)
    return f"""
LLVM has a missed optimization bug. Locate where to edit in InstCombine.

{issue_desc}

Choose exactly {LOCALIZE_TOP_K_FILES} files from the candidate list below, ranked by likelihood.
Output only file names from the list, one per line, wrapped in a ```text block.

Candidate files:
{candidate_lines}

Expected output format:
```text
InstCombineAddSub.cpp
InstCombineCompares.cpp
InstructionCombining.cpp
```
""".strip()


def _build_func_localize_prompt_lite(
    pred_file: str, issue_desc: str, file_text: str
) -> str:
    """Build lightweight function localization prompt."""
    file_text = _truncate_text(file_text, LOCALIZE_MAX_FILE_CHARS)
    return f"""
LLVM has a missed optimization bug.
The likely file is `{pred_file}`.

{issue_desc}

File content:
```cpp
{file_text}
```

Choose exactly {LOCALIZE_TOP_K_FUNCS} function names in this file, ranked by likelihood.
Output function names only, one per line, wrapped in a ```text block.

Expected output format:
```text
InstCombinerImpl::visitSub
InstCombinerImpl::visitAdd
InstCombinerImpl::visitMul
```
""".strip()


def _build_file_localize_prompt_pipeline(
    issue_desc: str, debug_output: str | None
) -> str:
    """Build pipeline/localize.py-style file localization prompt."""
    candidate_lines = "\n".join(os.path.basename(x) for x in LOCALIZE_CANDIDATE_FILES)
    debug_context = ""
    if debug_output:
        debug_context = f"""

**Debug Output Context**: When running the `opt` command on the source program, the following debug output was generated. This output may reveal which optimization passes, analyses, or code paths are involved in processing this program. Use this information to help identify which files are most likely responsible for the missed optimization:

```
{_truncate_text(debug_output, LOCALIZE_MAX_DEBUG_CHARS)}
```
"""

    analyze_section = (
        "### 1. Analyze\n"
        "**Content**: Provide a deep analysis of the missed optimization. Explain:"
    )
    if debug_output:
        analyze_section += (
            "\n- **Context**: Consider the debug output provided above, which "
            "shows the actual execution trace of LLVM optimization passes when "
            "processing this program."
        )
    analyze_section += """
- **What** the issue is (what optimization is missing or failing).
- **Why** it happens in the current LLVM implementation (e.g., which patterns, analyses, or passes are insufficient or incorrect).
- **Impact** on performance or correctness.
"""

    verify_section = (
        "### 3. Verify\n"
        f"**Content**: Justify why these {LOCALIZE_TOP_K_FILES} specific files are the most appropriate locations for the change:"
    )
    if debug_output:
        verify_section += (
            "\n- **Evidence from Debug Output**: If the debug output above "
            "mentions specific passes or components, explain how this supports "
            "your file selection."
        )
    verify_section += """
- Describe the responsibilities and typical transformations implemented in each file.
- Explain the ranking order (why the first file is more likely than the second, etc.).
"""

    prompt = f"""
LLVM is currently unable to perform this optimization successfully. Your task is to identify which files below are most likely to be modified to implement the missing optimization. Use the exact Markdown section headings and structure provided.

{issue_desc}{debug_context}

You must choose exactly {LOCALIZE_TOP_K_FILES} files from this list (ranked by likelihood):

{candidate_lines}

--

{analyze_section}

### 2. Propose positions
**Content**: You must output exactly {LOCALIZE_TOP_K_FILES} file names from the list above, ranked by likelihood (most likely first). Do not explain here; only output the chosen file names exactly as written in the list, one per line.

{verify_section}

### 4. Submit
**Content**: Provide the final answer by outputting file names (one per line), enclosed in a Markdown code block with language tag `text`, like:

```text
InstCombineXXX.cpp
InstCombineYYY.cpp
InstCombineZZZ.cpp
```
""".strip()
    return prompt


def _build_func_localize_prompt_pipeline(
    pred_file: str,
    full_path_rel: str,
    issue_desc: str,
    file_text: str,
) -> str:
    """Build pipeline/localize.py-style function localization prompt."""
    file_text = _truncate_text(file_text, LOCALIZE_MAX_FILE_CHARS)
    return f"""
LLVM is currently unable to perform this optimization successfully. We have identified that the file `{pred_file}` (full path: `{full_path_rel}`) is likely to contain the code that needs modification.

{issue_desc}

Below is the complete content of this file:
```cpp
{file_text}
```

### 1. Analyze
**Content**: Carefully read the file content and map functions to their likely responsibilities. Relate IR patterns in the source vs. expected optimized programs to the functions that typically handle those transformations.

### 2. Propose
**Content**: Identify the top {LOCALIZE_TOP_K_FUNCS} functions most likely responsible for the missed optimization. Give the function names exactly as they appear in the file. Do not include explanations in the final answer.

### 3. Submit
**Content**: Output function names (one per line), ranked by likelihood, wrapped in a Markdown code block with language tag `text`, like:

```text
InstCombinerImpl::visitMul
InstCombinerImpl::visitAdd
InstCombinerImpl::visitSub
```
""".strip()


def _get_opt_debug_output_pipeline(
    source_program: str,
    test_file: dict,
    build_dir: str,
) -> str | None:
    """Generate opt -debug stderr using pipeline/localize.py-compatible flow."""
    try:
        commands = test_file.get("commands", [])
        if not commands:
            return None

        first_cmd = commands[0]
        if isinstance(first_cmd, list):
            if not first_cmd:
                return None
            opt_cmd = first_cmd[0]
        else:
            opt_cmd = first_cmd

        if not isinstance(opt_cmd, str) or not opt_cmd.strip():
            return None

        tmp_dir = os.environ.get("LAB_TMP_DIR") or None
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".ll",
            delete=False,
            encoding="utf-8",
            dir=tmp_dir,
        ) as f:
            f.write(source_program)
            temp_file = f.name

        try:
            opt_path = os.path.join(build_dir, "bin", "opt")
            opt_cmd_parts = opt_cmd.replace("< ", " ").replace("%s", temp_file).split()
            if not opt_cmd_parts:
                return None

            replaced_opt = False
            for i, arg in enumerate(opt_cmd_parts):
                if arg == "opt" or arg.endswith("/opt"):
                    opt_cmd_parts[i] = opt_path
                    replaced_opt = True
                    break
            if not replaced_opt:
                opt_cmd_parts.insert(0, opt_path)

            if "-S" in opt_cmd_parts:
                s_idx = opt_cmd_parts.index("-S")
                opt_cmd_parts.insert(s_idx, "-debug")
            else:
                opt_cmd_parts.append("-debug")

            result = subprocess.run(
                opt_cmd_parts,
                capture_output=True,
                text=True,
                timeout=LOCALIZE_OPT_DEBUG_TIMEOUT,
                check=False,
            )
            return result.stderr if result.stderr else None
        finally:
            try:
                os.unlink(temp_file)
            except Exception:
                pass
    except Exception:
        return None


def _collect_pipeline_debug_output(
    env: Env,
    issue_id: str,
    source_program: str,
    test_file: dict,
    build_dir: str,
) -> str | None:
    """Collect optional opt -debug context for pipeline localization mode."""
    if not LOCALIZE_ENABLE_OPT_DEBUG:
        print(
            "[Localize][pipeline] opt -debug disabled by LAB_AGENT_LOCALIZE_ENABLE_OPT_DEBUG."
        )
        return None

    if LOCALIZE_BUILD_BEFORE_OPT_DEBUG:
        print(f"[Localize][pipeline] Building LLVM for {issue_id} before opt -debug...")
        try:
            build_res, _build_log = env.build()
        except Exception as exc:
            print(f"[Localize][pipeline] Build/debug failed for {issue_id}: {exc}")
            return None
        if not build_res:
            print(
                f"[Localize][pipeline] Build failed for {issue_id}; "
                "debug output disabled for this issue."
            )
            return None

    debug_output = _get_opt_debug_output_pipeline(
        source_program=source_program,
        test_file=test_file,
        build_dir=build_dir,
    )
    if debug_output:
        print(f"[Localize][pipeline] Debug output obtained (len={len(debug_output)})")
    else:
        print("[Localize][pipeline] No debug output")
    return debug_output


def _normalize_localize_model_name(raw_name: str, api_url: str) -> str:
    """Normalize model id for OpenAI chat.completions client."""
    model = raw_name.strip()
    if not model:
        return model
    if _is_official_openai_endpoint(api_url) and "/" in model:
        return model.split("/", 1)[1]
    return model


def _call_localize_model(prompt: str, *, sys_prompt: str) -> str:
    """Call localization model (best effort)."""
    try:
        from openai import OpenAI
    except Exception as exc:
        print(f"[Localize] Skip runtime localization: OpenAI SDK unavailable ({exc})")
        return ""

    try:
        client_kwargs: dict[str, object] = {}
        if cfg.llm_token:
            client_kwargs["api_key"] = cfg.llm_token
        if cfg.llm_url:
            client_kwargs["base_url"] = cfg.llm_url

        client = OpenAI(**client_kwargs) if client_kwargs else OpenAI()
        model_name = _normalize_localize_model_name(cfg.llm_model, cfg.llm_url)
        if not model_name:
            return ""

        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=cfg.llm_temperature,
            timeout=LOCALIZE_TIMEOUT_SECONDS,
        )
        content = resp.choices[0].message.content
        return content if isinstance(content, str) else str(content or "")
    except Exception as exc:
        print(f"[Localize] Model call failed: {exc}")
        return ""


def _resolve_localize_file_path(pred_file: str) -> str | None:
    """Map model-predicted filename to canonical candidate file path."""
    value = pred_file.strip().strip("`").strip()
    if not value:
        return None

    if value in LOCALIZE_CANDIDATE_FILES:
        return value

    if value.startswith("llvm/"):
        base = os.path.basename(value)
    else:
        base = os.path.basename(value)

    for candidate in LOCALIZE_CANDIDATE_FILES:
        if os.path.basename(candidate) == base:
            return candidate
    return None


def _read_localize_file_text(issue_llvm_dir: str, rel_path: str) -> str:
    abs_path = os.path.join(issue_llvm_dir, rel_path)
    if not os.path.isfile(abs_path):
        return ""
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return ""
    if len(text) > LOCALIZE_MAX_FILE_CHARS:
        return text[:LOCALIZE_MAX_FILE_CHARS]
    return text


def _load_precomputed_localization(
    issue_id: str,
) -> tuple[list[str], dict[str, list[str]], str]:
    """Load issue-scoped localization cache from results/agent/<model>/<issue>/."""
    localize_output = os.path.join(
        _issue_results_dir(issue_id), LOCALIZE_CACHE_FILENAME
    )
    if not os.path.isfile(localize_output):
        return [], {}, localize_output

    try:
        with open(localize_output, "r", encoding="utf-8") as f:
            localize_data = json.load(f)
    except Exception as exc:
        print(f"[Localize] Failed to read {localize_output}: {exc}")
        return [], {}, localize_output

    issue_data = localize_data
    if isinstance(localize_data, dict) and (
        "pred_files" not in localize_data and "pred_funcs" not in localize_data
    ):
        issue_data = localize_data.get(issue_id)
    if not isinstance(issue_data, dict):
        return [], {}, localize_output

    pred_files: list[str] = []
    for raw_file in _normalize_str_list(
        issue_data.get("pred_files"), limit=LOCALIZE_TOP_K_FILES
    ):
        resolved = _resolve_localize_file_path(raw_file) or raw_file
        if resolved not in pred_files:
            pred_files.append(resolved)

    pred_funcs: dict[str, list[str]] = {}
    for raw_file, funcs in _normalize_pred_funcs(issue_data.get("pred_funcs")).items():
        resolved = _resolve_localize_file_path(raw_file) or raw_file
        pred_funcs[resolved] = funcs[:LOCALIZE_TOP_K_FUNCS]

    return pred_files, pred_funcs, localize_output


def _persist_issue_localization(
    issue_id: str,
    pred_files: list[str],
    pred_funcs: dict[str, list[str]],
    *,
    localize_mode: str,
    source: str,
) -> str:
    """Persist runtime localization result to issue-scoped cache file."""
    localize_output = os.path.join(
        _issue_results_dir(issue_id), LOCALIZE_CACHE_FILENAME
    )
    payload = {
        "issue_id": issue_id,
        "pred_files": _normalize_str_list(pred_files, limit=LOCALIZE_TOP_K_FILES),
        "pred_funcs": {},
        "localize_mode": localize_mode,
        "source": source,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }

    normalized_funcs = _normalize_pred_funcs(pred_funcs)
    for raw_file, funcs in normalized_funcs.items():
        resolved = _resolve_localize_file_path(raw_file) or raw_file
        payload["pred_funcs"][resolved] = funcs[:LOCALIZE_TOP_K_FUNCS]

    try:
        with open(localize_output, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"[Localize] Persisted issue localization: {localize_output}")
    except Exception as exc:
        print(f"[Localize] Failed to persist {localize_output}: {exc}")

    return localize_output


def _run_runtime_localization_lite(
    issue_id: str,
    issue_desc: str,
    issue_llvm_dir: str,
) -> tuple[list[str], dict[str, list[str]]]:
    """Run lightweight (file -> function) runtime localization."""
    if not LOCALIZE_RUNTIME_ENABLED:
        return [], {}
    if not issue_desc.strip():
        return [], {}

    print(f"[Localize][lite] Running runtime localization for issue {issue_id}...")

    file_prompt = _build_file_localize_prompt_lite(issue_desc)
    file_output = _call_localize_model(file_prompt, sys_prompt=LOCALIZE_SYS_PROMPT_FILE)
    raw_files = _extract_topk_lines_from_model_output(file_output, LOCALIZE_TOP_K_FILES)

    pred_files: list[str] = []
    pred_funcs: dict[str, list[str]] = {}
    for raw_file in raw_files:
        resolved = _resolve_localize_file_path(raw_file)
        if not resolved:
            continue
        if resolved not in pred_files:
            pred_files.append(resolved)

        file_text = _read_localize_file_text(issue_llvm_dir, resolved)
        if not file_text:
            continue

        func_prompt = _build_func_localize_prompt_lite(
            pred_file=os.path.basename(resolved),
            issue_desc=issue_desc,
            file_text=file_text,
        )
        func_output = _call_localize_model(
            func_prompt, sys_prompt=LOCALIZE_SYS_PROMPT_FUNC
        )
        funcs = _extract_topk_lines_from_model_output(func_output, LOCALIZE_TOP_K_FUNCS)
        if funcs:
            pred_funcs[resolved] = funcs

    if pred_files:
        print(f"[Localize][lite] Runtime candidate files: {pred_files}")
    if pred_funcs:
        print(f"[Localize][lite] Runtime candidate funcs: {pred_funcs}")

    return pred_files, pred_funcs


def _run_runtime_localization_pipeline(
    issue_id: str,
    env: Env,
    issue_llvm_dir: str,
    build_dir: str,
) -> tuple[list[str], dict[str, list[str]]]:
    """Run pipeline/localize.py-aligned runtime localization."""
    if not LOCALIZE_RUNTIME_ENABLED:
        return [], {}

    case = _get_first_missed_optimization_test_case(env)
    if not case:
        return [], {}

    source_program, current_optimized_program, expect_optimized_program, test_file = (
        case
    )
    issue_desc = _build_pipeline_issue_desc(
        source_program=source_program,
        current_optimized_program=current_optimized_program,
        expect_optimized_program=expect_optimized_program,
    )
    if not issue_desc.strip():
        return [], {}

    print(f"[Localize][pipeline] Running runtime localization for issue {issue_id}...")
    debug_output = _collect_pipeline_debug_output(
        env=env,
        issue_id=issue_id,
        source_program=source_program,
        test_file=test_file,
        build_dir=build_dir,
    )

    file_prompt = _build_file_localize_prompt_pipeline(issue_desc, debug_output)
    file_output = _call_localize_model(file_prompt, sys_prompt=LOCALIZE_SYS_PROMPT_FILE)
    raw_files = _extract_topk_lines_from_model_output(file_output, LOCALIZE_TOP_K_FILES)

    pred_files: list[str] = []
    pred_funcs: dict[str, list[str]] = {}
    for raw_file in raw_files:
        resolved = _resolve_localize_file_path(raw_file)
        if not resolved:
            continue
        if resolved not in pred_files:
            pred_files.append(resolved)

        file_text = _read_localize_file_text(issue_llvm_dir, resolved)
        if not file_text:
            continue

        func_prompt = _build_func_localize_prompt_pipeline(
            pred_file=os.path.basename(resolved),
            full_path_rel=resolved,
            issue_desc=issue_desc,
            file_text=file_text,
        )
        func_output = _call_localize_model(
            func_prompt, sys_prompt=LOCALIZE_SYS_PROMPT_FUNC
        )
        funcs = _extract_topk_lines_from_model_output(func_output, LOCALIZE_TOP_K_FUNCS)
        if funcs:
            pred_funcs[resolved] = funcs

    if pred_files:
        print(f"[Localize][pipeline] Runtime candidate files: {pred_files}")
    if pred_funcs:
        print(f"[Localize][pipeline] Runtime candidate funcs: {pred_funcs}")

    return pred_files, pred_funcs


def _run_runtime_localization(
    issue_id: str,
    issue_desc: str,
    issue_llvm_dir: str,
    env: Env,
    build_dir: str,
    localize_mode: str,
) -> tuple[list[str], dict[str, list[str]]]:
    """Run runtime localization dispatcher by selected mode."""
    if localize_mode == LOCALIZE_MODE_PIPELINE:
        return _run_runtime_localization_pipeline(
            issue_id=issue_id,
            env=env,
            issue_llvm_dir=issue_llvm_dir,
            build_dir=build_dir,
        )
    if localize_mode == LOCALIZE_MODE_LITE:
        return _run_runtime_localization_lite(
            issue_id=issue_id,
            issue_desc=issue_desc,
            issue_llvm_dir=issue_llvm_dir,
        )
    print(f"[Localize] Unknown mode={localize_mode!r}, fallback to lite.")
    return _run_runtime_localization_lite(
        issue_id=issue_id,
        issue_desc=issue_desc,
        issue_llvm_dir=issue_llvm_dir,
    )


def build_task_description(
    env: Env,
    issue_id: str,
    issue_llvm_dir: str,
    build_dir: str,
    localize_mode: str,
) -> str:
    """构造传给 agent.run(task=...) 的任务描述文本。"""
    bug_type = env.get_bug_type()
    components = list(env.get_hint_components() or [])
    component = components[0] if components else "Unknown"
    bug_funcs = env.get_hint_bug_functions() or {}
    hints = env.data.get("hints", {}) if isinstance(env.data, dict) else {}
    bug_lines = hints.get("bug_location_lineno", {}) if isinstance(hints, dict) else {}

    issue_desc = _first_missed_optimization_desc(env)

    desc = f"**Issue ID**: {issue_id}\n"
    desc += f"**Bug Type**: {bug_type}\n"
    desc += f"**Component**: {component}\n"

    if bug_funcs:
        for fname, funcs in bug_funcs.items():
            desc += f"**Hint — Bug Location**: {fname} → {', '.join(funcs)}\n"

    if isinstance(bug_lines, dict):
        for fname, ranges in bug_lines.items():
            if not isinstance(fname, str):
                continue
            if not isinstance(ranges, list):
                continue
            render_ranges: list[str] = []
            for item in ranges:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    start, end = item
                    if isinstance(start, int) and isinstance(end, int):
                        render_ranges.append(f"{start}-{end}")
            if render_ranges:
                desc += f"**Hint — Bug Lines**: {fname} → {', '.join(render_ranges)}\n"

    desc += f"\n{issue_desc}"

    pred_files, pred_funcs, localize_output = _load_precomputed_localization(issue_id)
    localize_source = ""
    if pred_files or pred_funcs:
        localize_source = f"issue localization cache ({localize_output})"
    else:
        print(
            f"[Localize] No issue cache for {issue_id}: {localize_output}; "
            f"fallback to runtime ({localize_mode})."
        )
        pred_files, pred_funcs = _run_runtime_localization(
            issue_id=issue_id,
            issue_desc=issue_desc,
            issue_llvm_dir=issue_llvm_dir,
            env=env,
            build_dir=build_dir,
            localize_mode=localize_mode,
        )
        if LOCALIZE_RUNTIME_ENABLED:
            _persist_issue_localization(
                issue_id=issue_id,
                pred_files=pred_files,
                pred_funcs=pred_funcs,
                localize_mode=localize_mode,
                source=f"runtime localization ({localize_mode})",
            )
        if pred_files or pred_funcs:
            localize_source = f"runtime localization ({localize_mode})"
        else:
            print(
                f"[Localize] Runtime localization returned empty for issue {issue_id}."
            )

    if pred_files or pred_funcs:
        desc += f"\n**Localization Predictions** ({localize_source}):\n"

        if pred_files:
            desc += "  - Candidate files (ranked):\n"
            for idx, fpath in enumerate(pred_files, 1):
                desc += f"    {idx}. {fpath}\n"

        ordered_files: list[str] = list(pred_files)
        for fpath in pred_funcs:
            if fpath not in ordered_files:
                ordered_files.append(fpath)

        if pred_funcs:
            desc += "  - Candidate functions (ranked in each file):\n"
            for fpath in ordered_files:
                fnames = pred_funcs.get(fpath, [])
                if fnames:
                    desc += f"    - {fpath}: {', '.join(fnames)}\n"

        if ordered_files:
            primary_file = ordered_files[0]
            primary_funcs = pred_funcs.get(primary_file, [])
            if primary_funcs:
                desc += (
                    "\n**Localization Guidance**: "
                    f"Start from `{primary_file}` and `{primary_funcs[0]}` first. "
                    "If no progress after 2 iterations, move to the next ranked candidate.\n"
                )
            else:
                desc += (
                    "\n**Localization Guidance**: "
                    f"Start from `{primary_file}` first, then continue by rank.\n"
                )

    return desc


def setup_build_cache(issue_id: str, env: Env, issue_llvm_dir: str) -> str:
    """初始化构建缓存（复用 generate.py 逻辑），返回 build_dir。"""
    build_base = cfg.llvm_build_dir
    build_dir = os.path.join(build_base, issue_id)
    cache_dir = os.path.join(build_base, issue_id + "_cache")

    # 临时设置 llvm_helper 的全局路径
    llvm_helper.llvm_dir = issue_llvm_dir
    llvm_helper.llvm_build_dir = build_dir
    os.environ["LAB_LLVM_DIR"] = issue_llvm_dir

    if os.path.exists(cache_dir):
        shutil.rmtree(build_dir, ignore_errors=True)
        shutil.copytree(cache_dir, build_dir)
        res, log = env.check_fast(skip_build=True)
        print(f"[Cache] Fast check from cache: {'PASS' if res else 'FAIL'}")
    else:
        res, log = env.check_fast(skip_build=False)
        print(f"[Build] Initial fast check: {'PASS' if res else 'FAIL'}")
        # 保存缓存
        shutil.copytree(build_dir, cache_dir)

    if res:
        print(
            f"WARNING: Issue {issue_id} passes fast check without any fix — skipping."
        )
        return None

    return build_dir


def convert_to_pipeline_format(
    env: Env,
    agent_messages: list[dict],
    trajectory_path: str | None,
    model_name: str,
    start_time: float,
    exit_status: str = "",
    submission: str = "",
) -> dict:
    """将 agent 运行结果转换为与 pipeline/generate.py 兼容的 JSON 格式。

    新增 exit_status / submission 字段，与 .traj.json 中 info 区段对齐。
    """
    wall_time = time.time() - start_time

    # 获取 patch
    try:
        env.verify_head()
        patch = llvm_helper.git_execute(["diff", "--", "llvm/lib/*", "llvm/include/*"])
    except Exception:
        patch = ""

    # 知识使用记录
    used_knowledge = []
    for url_key, t in env.used_knowledge.items():
        used_knowledge.append((url_key, t.strftime("%Y-%m-%d%z")))

    result = {
        "wall_time": wall_time,
        "knowledge": used_knowledge,
        "build_count": env.build_count,
        "build_failure_count": env.build_failure_count,
        "fast_check_count": env.fast_check_count,
        "full_check_count": env.full_check_count,
        "fast_check_pass": env.fast_check_pass,
        "full_check_pass": env.full_check_pass,
        "patch": patch,
        "exit_status": exit_status,
        "submission": submission,
        "log": {
            "model": model_name,
            "messages": agent_messages,
            "trajectory": trajectory_path,
        },
        "check_fast_log": env.check_fast_log,
        "check_full_log": env.check_full_log,
    }

    # 与 pipeline/generate.py 对齐：当 fast 通过但 full 失败时，
    # retest_patches.py 读取 fast_full_mismatch_patch 作为复测补丁来源。
    if env.fast_check_pass and not env.full_check_pass and patch:
        result["fast_full_mismatch_patch"] = patch
        result["fast_full_mismatch_full_check_count"] = env.full_check_count

    return result


# ──────────────────────────────────────────────────────────────────────
# 运行态辅助（重试判定 / 提交门槛）
# ──────────────────────────────────────────────────────────────────────


def _extract_text_messages(messages: list[dict]) -> list[str]:
    texts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            texts.append(content)
    return texts


def _has_fast_or_full_pass_signal(messages: list[dict]) -> bool:
    texts = _extract_text_messages(messages)
    for text in texts:
        if (
            "BUILD SUCCESS + FAST CHECK PASSED" in text
            or "FAST CHECK PASSED" in text
            or "FULL CHECK PASSED" in text
        ):
            return True
    return False


def _should_retry_after_limits(exit_status: str, messages: list[dict]) -> bool:
    if not LIMITS_RETRY_ENABLED:
        return False
    if exit_status != "LimitsExceeded":
        return False
    return _has_fast_or_full_pass_signal(messages)


# ──────────────────────────────────────────────────────────────────────
# 构造 litellm 兼容的 model_name
# ──────────────────────────────────────────────────────────────────────


def _is_official_openai_endpoint(api_url: str) -> bool:
    """判断是否 OpenAI 官方端点。"""
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


def make_litellm_model_name(raw_name: str, api_url: str) -> str:
    """根据 LAB_LLM_MODEL 和 LAB_LLM_URL 构造 litellm 兼容的 model name。

    - 对自建 OpenAI-compatible 端点：
      1) LAB_LLM_MODEL 使用 .env 中的原始值（既支持带前缀，也支持无前缀）。
      2) 不猜测/补全供应商前缀，直接使用 .env 中给出的模型名。
      3) 为兼容 litellm 的 custom_openai 路由，包装为 `custom_openai/<raw_model>`
         （若已是 custom_openai/... 则原样使用）。
    - 对 OpenAI 官方端点，保持 litellm 既有约定：
      若未带供应商前缀则补 `openai/`。
    """
    model = raw_name.strip()
    if not model:
        raise ValueError("LAB_LLM_MODEL 不能为空。")

    # 自建端点：保留 .env 模型名原样（可有前缀也可无前缀），并走 custom_openai。
    if not _is_official_openai_endpoint(api_url):
        if model.startswith("custom_openai/"):
            return model
        return f"custom_openai/{model}"

    # OpenAI 官方端点保持原有行为
    if "/" in model:
        return model
    return f"openai/{model}"


# ──────────────────────────────────────────────────────────────────────
# 主逻辑
# ──────────────────────────────────────────────────────────────────────


def fix_issue(
    issue_id: str,
    override: bool = False,
    *,
    model_name: str,
    localize_mode: str,
    fresh_run: bool = False,
    thinking_overrides: dict[str, object] | None = None,
):
    """用 mini-swe-agent 修复单个 issue。"""
    tmp_dir = os.environ.get("LAB_TMP_DIR", cfg.tmp_dir)
    if not tmp_dir:
        tmp_dir = cfg.tmp_dir
    tmp_dir = _ensure_writable_tmp_dir(tmp_dir)
    os.environ["LAB_TMP_DIR"] = tmp_dir
    os.environ["TMPDIR"] = tmp_dir

    run_output_dir, fix_log_path, traj_path, preds_path = _build_output_paths(issue_id)

    if not override and os.path.exists(fix_log_path):
        print(f"Skip {issue_id} (result exists, use -f to override)")
        return

    print(f"\n{'='*60}")
    print(f"Fixing {issue_id}")
    print(f"{'='*60}")

    start_time = time.time()

    if fresh_run:
        _clean_issue_workspace(issue_id)

    # 1. 准备 per-issue LLVM 克隆
    issue_llvm_dir = ensure_llvm_clone_for_issue(issue_id, cfg.llvm_dir)
    llvm_helper.llvm_dir = issue_llvm_dir
    os.environ["LAB_LLVM_DIR"] = issue_llvm_dir

    # 2. 加载 issue 数据
    env = Env(issue_id, cfg.llm_basemodel_cutoff, max_build_jobs=cfg.max_build_jobs)
    verified = env.data.get("verified", False)
    if not verified:
        print(f"Issue {issue_id} is not verified — skipping")
        return

    # 3. 初始化构建缓存
    env.reset()
    build_dir = setup_build_cache(issue_id, env, issue_llvm_dir)
    if build_dir is None:
        return

    # 重置 env 统计（setup_build_cache 会增加 build/check 计数）
    env.build_count = 0
    env.build_failure_count = 0
    env.fast_check_count = 0
    env.full_check_count = 0
    env.fast_check_pass = False
    env.full_check_pass = False
    env.check_fast_log = []
    env.check_full_log = []

    # 重置到 base commit
    env.reset()

    # 4. 构造任务描述（含强化定位信息）
    task = build_task_description(
        env=env,
        issue_id=issue_id,
        issue_llvm_dir=issue_llvm_dir,
        build_dir=build_dir,
        localize_mode=localize_mode,
    )

    # 5. 创建 mini-swe-agent 组件
    litellm_name = make_litellm_model_name(model_name, cfg.llm_url)
    model_kwargs = {
        "temperature": 1.0,
        "timeout": 300,
    }
    # 为 OpenAI-compatible 端点设置 api_base 和 api_key
    if cfg.llm_url and not _is_official_openai_endpoint(cfg.llm_url):
        model_kwargs["api_base"] = cfg.llm_url
    if cfg.llm_token:
        model_kwargs["api_key"] = cfg.llm_token

    if thinking_overrides:
        _deep_merge_dict(model_kwargs, thinking_overrides)

    # 对 custom_openai 路由，OpenAI SDK 的 completions.create 不接受顶层 reasoning 参数；
    # 需透传到 extra_body，避免 `unexpected keyword argument 'reasoning'`。
    if litellm_name.startswith("custom_openai/") and "reasoning" in model_kwargs:
        reasoning_cfg = model_kwargs.pop("reasoning")
        extra_body_cfg = model_kwargs.setdefault("extra_body", {})
        if not isinstance(extra_body_cfg, dict):
            raise ValueError("model_kwargs.extra_body 必须是字典，无法注入 reasoning。")
        existing_reasoning = extra_body_cfg.get("reasoning")
        if isinstance(existing_reasoning, dict) and isinstance(reasoning_cfg, dict):
            _deep_merge_dict(existing_reasoning, reasoning_cfg)
        else:
            extra_body_cfg["reasoning"] = reasoning_cfg

    # 对未被 litellm 收录定价表的自定义模型，忽略成本估算错误以避免中断主流程。
    cost_tracking_mode = os.environ.get("MSWEA_COST_TRACKING", "ignore_errors")

    model = get_model(
        input_model_name=litellm_name,
        config={
            "model_kwargs": model_kwargs,
            "cost_tracking": cost_tracking_mode,
            "observation_template": (
                "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n"
                "{% endif %}<returncode>{{output.returncode}}</returncode>\n"
                "<output>\n"
                "{% if output.output | length > 15000 %}"
                "{{ output.output[:7500] }}\n"
                "<truncated — showing first 7500 and last 7500 chars of {{ output.output | length }}>\n"
                "{{ output.output[-7500:] }}"
                "{% else %}{{ output.output }}{% endif %}\n"
                "</output>"
            ),
            "format_error_template": (
                "Your last action was not in the correct format.\n"
                "{{ error }}\n"
                "Please use the bash tool to execute commands. "
                "Every command should start with `cd " + issue_llvm_dir + " && ...`."
            ),
        },
    )

    llvm_env = LLVMEnvironment(
        issue_id=issue_id,
        issue_llvm_dir=issue_llvm_dir,
        llvm_build_dir=build_dir,
        tools_dir=cfg.tools_dir,
        timeout=600,
    )

    # 系统和实例模板
    system_tpl = _load_system_template()
    instance_tpl = _load_instance_template()

    agent = LLVMFixAgent(
        model,
        llvm_env,
        system_template=system_tpl,
        instance_template=instance_tpl,
        step_limit=cfg.step_limit,
        cost_limit=cfg.cost_limit,
        output_path=Path(traj_path),
    )

    # 6. 运行 agent
    print(f"Running agent (step_limit={cfg.step_limit}, cost_limit={cfg.cost_limit})")
    print(f"Model: {litellm_name}")
    print(f"LLVM dir: {issue_llvm_dir}")
    print(f"Build dir: {build_dir}")
    print(f"Output dir: {run_output_dir}")
    print()

    exit_status = "Error"
    submission = ""
    agent_messages: list[dict] = []
    try:
        result = agent.run(
            task=task,
            issue_id=issue_id,
            cwd=issue_llvm_dir,
            model_name=litellm_name,
        )
        exit_status = result.get("exit_status", "Unknown")
        submission = result.get("submission", "")
    except Exception as e:
        print(f"Agent error: {e}")
        import traceback

        traceback.print_exc()
        result = {}
    if hasattr(agent, "messages"):
        agent_messages = agent.messages

    if _should_retry_after_limits(exit_status, agent_messages):
        retry_step_limit = cfg.step_limit + LIMITS_RETRY_EXTRA_STEPS
        print(
            f"[Retry] exit_status=LimitsExceeded but pass signals detected; "
            f"rerun with step_limit={retry_step_limit}"
        )
        retry_agent = LLVMFixAgent(
            model,
            llvm_env,
            system_template=system_tpl,
            instance_template=instance_tpl,
            step_limit=retry_step_limit,
            cost_limit=cfg.cost_limit,
            output_path=Path(traj_path),
        )
        try:
            retry_result = retry_agent.run(
                task=task,
                issue_id=issue_id,
                cwd=issue_llvm_dir,
                model_name=litellm_name,
            )
            exit_status = retry_result.get("exit_status", "Unknown")
            submission = retry_result.get("submission", "")
            agent = retry_agent
            if hasattr(agent, "messages"):
                agent_messages = agent.messages
        except Exception as e:
            print(f"[Retry] Agent retry error: {e}")
            import traceback

            traceback.print_exc()

    # 7. 运行完成后，用 env 做最终验证（复用 lab_env 逻辑确保一致性）
    # 重新加载 env 以获取最新状态
    llvm_helper.llvm_dir = issue_llvm_dir
    llvm_helper.llvm_build_dir = build_dir
    final_env = Env(
        issue_id, cfg.llm_basemodel_cutoff, max_build_jobs=cfg.max_build_jobs
    )

    # 快速检查
    try:
        fast_res, fast_log = final_env.check_fast(skip_build=False)
        print(f"\n[Final] Fast check: {'PASS' if fast_res else 'FAIL'}")
    except Exception as e:
        print(f"\n[Final] Fast check error: {e}")
        fast_res = False

    # 如果快速检查通过，再做完整检查
    if fast_res:
        try:
            full_res, full_log = final_env.check_full()
            print(f"[Final] Full check: {'PASS' if full_res else 'FAIL'}")
        except Exception as e:
            print(f"[Final] Full check error: {e}")

    # 提交硬门槛：若最终 fast/full 未通过，则不接受 submitted。
    if exit_status == "submitted" and not (
        final_env.fast_check_pass and final_env.full_check_pass
    ):
        print(
            "[SubmitGate] Blocked invalid submission: "
            "final fast/full checks are not both PASS."
        )
        exit_status = "SubmitBlocked"
        submission = ""

    # 8. 转换并保存结果
    if hasattr(agent, "messages"):
        agent_messages = agent.messages

    cert = convert_to_pipeline_format(
        final_env,
        agent_messages,
        traj_path if os.path.exists(traj_path) else None,
        litellm_name,
        start_time,
        exit_status=exit_status,
        submission=submission,
    )

    with open(fix_log_path, "w") as f:
        f.write(json.dumps(cert, indent=2, default=str))

    # 增量更新 preds.json（聚合预测文件，与 mini-swe-agent 规范对齐）
    _update_preds_json(issue_id, cert, litellm_name, preds_path)

    print(f"\n[Result] Saved to {fix_log_path}")
    if os.path.exists(traj_path):
        print(f"[Result] Trajectory: {traj_path}")
    if os.path.exists(preds_path):
        print(f"[Result] Preds: {preds_path}")
    print(f"  exit_status: {exit_status}")
    print(f"  fast_check_pass: {cert['fast_check_pass']}")
    print(f"  full_check_pass: {cert['full_check_pass']}")
    print(f"  wall_time: {cert['wall_time']:.1f}s")
    if os.path.exists(traj_path):
        print(f"  trajectory: {traj_path}")


# ──────────────────────────────────────────────────────────────────────
# preds.json 聚合（与 mini-swe-agent Output files 规范对齐）
# ──────────────────────────────────────────────────────────────────────


def _update_preds_json(issue_id: str, cert: dict, model_name: str, preds_path: str):
    """增量更新单次运行目录下的 preds.json，汇总本次 issue 的最终 patch。

    格式参考 mini-swe-agent 的 preds.json 规范：
      { "<issue_id>": { "model_name_or_path": "...", "instance_id": "...", "model_patch": "..." } }
    """
    preds = {}
    if os.path.exists(preds_path):
        try:
            with open(preds_path, "r") as f:
                preds = json.load(f)
        except (json.JSONDecodeError, OSError):
            preds = {}

    preds[issue_id] = {
        "model_name_or_path": model_name,
        "instance_id": issue_id,
        "model_patch": cert.get("patch", ""),
    }

    with open(preds_path, "w") as f:
        json.dump(preds, f, indent=2)


# ──────────────────────────────────────────────────────────────────────
# 模板加载
# ──────────────────────────────────────────────────────────────────────


def _load_system_template() -> str:
    """加载 system prompt 模板。"""
    return """\
You are an expert LLVM compiler maintainer specializing in InstCombine and other optimization passes.
Users have reported a case where LLVM failed to optimize a specific program (missed optimization).
Your task is to locate the relevant code and implement the missing optimization.

## Available Tools (call via bash)

You have access to the following CLI tools in `$AGENT_TOOLS_DIR`.
All tools are Python scripts — call them with `cd {{cwd}} && python $AGENT_TOOLS_DIR/<script>.py <args>`.

1. **issue_info.py {{issue_id}}** — View issue metadata, bug type, component, and test cases (source/expected/current IR).
2. **view_source.py <file> [start_line] [end_line]** — View LLVM source code with line numbers.
   Example: `cd {{cwd}} && python $AGENT_TOOLS_DIR/view_source.py llvm/lib/Transforms/InstCombine/InstCombineCompares.cpp 100 200`
3. **apply_code.py** — Modify LLVM source files. Three modes:
   - `cat <<'CODEEOF' | python $AGENT_TOOLS_DIR/apply_code.py write <file> <start_line> <end_line>\\n<new_code>\\nCODEEOF` — Replace line range
   - `python $AGENT_TOOLS_DIR/apply_code.py sed <file> '<old_string>' '<new_string>'` — Simple string replacement
4. **build_and_check.py** — Build LLVM and run fast verification (build + test group check).
5. **check_full.py** — Run full verification (build + test group + llvm-lit regression tests).
6. **alive2_check.py <src.ll> <tgt.ll>** — Alive2 semantic verification of an optimization.
7. **get_langref.py <instruction>** — Query LLVM LangRef documentation for an IR instruction/intrinsic.
8. **show_diff.py** — Show current git diff for llvm/lib/ and llvm/include/.

You can also use standard bash commands (grep, find, cat, head, tail, etc.) for exploration.

## Workflow

1. **Understand**: Read the task description to understand the missed optimization. If needed, run `issue_info.py` for more details.
2. **Locate**: Find the relevant source file and function using hints, grep, or your LLVM knowledge.
3. **Analyze**: Study the existing code to understand why the optimization is missing.
4. **Implement**: Make minimal, targeted changes using `apply_code.py` or direct file edits.
5. **Build & Test Frequently**: After each code edit (every `apply_code.py write/sed`), immediately run `cd {{cwd}} && python $AGENT_TOOLS_DIR/build_and_check.py`.
6. **Iterate**: If tests fail, analyze errors and refine your patch.
7. **Regression Test**: Once fast checks pass, run `cd {{cwd}} && python $AGENT_TOOLS_DIR/check_full.py`.
8. **Submit**: When all checks pass, run `echo SUBMIT_PATCH` to finalize.

## Strict Execution Rules

- Start from the top-ranked localization candidate; do not browse more than 2 files before the first code edit.
- Within the first 8 actions, you must make at least one `apply_code.py` edit and run `build_and_check.py`.
- Never run more than 3 consecutive exploration actions (`view_source.py`, `grep`, `find`) without either `apply_code.py` or `build_and_check.py`.
- After every `apply_code.py` edit, run `build_and_check.py` immediately before more exploration.
- If fast check passes, run `check_full.py` immediately in the same iteration.
- Do not run `echo SUBMIT_PATCH` unless both fast and full checks have passed after the latest code edit.

## Important Constraints

- Each bash command runs in a **fresh subshell** — always prefix with `cd {{cwd}} && `.
- Only modify files under `llvm/lib/` and `llvm/include/`.
- Make minimal changes — do not refactor unrelated code.
- Run fast checks multiple times during the run; do not batch many edits before verification.
- If a build fails, carefully read error messages before making corrections.
- When fast check passes but lit regression fails, your optimization is correct but causes
  behavior changes — adjust to preserve existing behavior while keeping the optimization."""


def _load_instance_template() -> str:
    """加载 instance prompt 模板。"""
    return """\
## Task: Fix Missed Optimization

{{task}}

## Getting Started

Start by examining the test cases in the task description above. Then locate the relevant source code.
Use `cd {{cwd}} && python $AGENT_TOOLS_DIR/view_source.py <file> <start> <end>` to browse the code.
Use `cd {{cwd}} && grep -rn '<pattern>' llvm/lib/Transforms/` to search for relevant code.

When ready, make your changes. After each `apply_code.py` edit, immediately run
`cd {{cwd}} && python $AGENT_TOOLS_DIR/build_and_check.py`.
Repeat this edit -> fast-check cycle as needed before `check_full.py`.

Hard constraints:
- First edit + first `build_and_check.py` must happen within 8 actions.
- At most 3 consecutive exploration commands (`view_source.py`, `grep`, `find`) are allowed.
- `echo SUBMIT_PATCH` is only allowed after `build_and_check.py` and `check_full.py` both pass after the latest edit."""


# ──────────────────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = _parse_cli_args(sys.argv[1:])
    if args.retest_force or args.retest_dir.strip():
        args.retest_patches = True

    if args.model.strip():
        cfg.llm_model = args.model.strip()

    try:
        thinking_overrides = _build_model_thinking_overrides(args, cfg.llm_model)
    except ValueError as exc:
        print(f"[ERROR] Invalid model/thinking parameters: {exc}")
        sys.exit(2)
    try:
        localize_mode, localize_mode_source = _resolve_localize_mode(args.localize_mode)
    except ValueError as exc:
        print(f"[ERROR] Invalid localization mode: {exc}")
        sys.exit(2)

    override = args.force
    issues_arg = args.issues.strip()

    if not issues_arg or issues_arg.lower() == "all":
        # 处理全部 issue
        task_list = _list_dataset_issue_ids()
    else:
        # 处理指定 issue（支持逗号分隔）
        ids = issues_arg.split(",")
        task_list = [x.strip().removesuffix(".json") for x in ids if x.strip()]

    # 仅保留 dataset/ 中存在的 issue，避免误跑历史/外部数据。
    filtered_task_list: list[str] = []
    for task_id in task_list:
        dataset_file = os.path.join(llvm_helper.dataset_dir, f"{task_id}.json")
        if os.path.isfile(dataset_file):
            filtered_task_list.append(task_id)
        else:
            print(f"[WARN] Skip unknown issue {task_id}: not found in dataset/")
    task_list = filtered_task_list

    if not task_list:
        print("No valid issues found in dataset/ to process")
        sys.exit(0)

    # 复测 issue 选择：all 模式让 agent/retest_patches.py 按 patch 目录自动枚举；
    # 指定 issue 模式下仅复测同一批 issue。
    retest_issue_ids = (
        None if (not issues_arg or issues_arg.lower() == "all") else task_list
    )

    print(f"Agent workflow — {len(task_list)} issue(s) to process")
    print(f"Model: {cfg.llm_model}")
    print(f"Localization mode: {localize_mode} (source={localize_mode_source})")
    print("Thinking overrides:")
    if thinking_overrides:
        print(json.dumps(thinking_overrides, indent=2, ensure_ascii=False))
    print(f"Results root: {RESULTS_AGENT_DIR}")
    print(f"Run timestamp: {cfg.run_timestamp}")
    print(f"Fresh run: {args.fresh_run}")
    if args.retest_patches:
        target_retest_root = args.retest_dir.strip() or _default_retest_root()
        print("Retest with agent/retest_patches.py: enabled")
        print(f"Retest output root: {target_retest_root}")
        print("Retest file pattern: <retest_root>/<safe_issue>/<timestamp>/retest.json")
    print()

    retest_issue_ids = []
    for task_id in task_list:
        try:
            fix_issue(
                task_id,
                override=override,
                model_name=cfg.llm_model,
                localize_mode=localize_mode,
                fresh_run=args.fresh_run,
                thinking_overrides=thinking_overrides,
            )
            retest_issue_ids.append(task_id)
        except Exception as e:
            print(f"ERROR processing {task_id}: {e}")
            import traceback

            traceback.print_exc()

    if args.retest_patches:
        retest_code = _run_agent_retest(
            retest_issue_ids,
            override=args.retest_force,
            retest_root=args.retest_dir,
        )
        if retest_code != 0:
            sys.exit(retest_code)
