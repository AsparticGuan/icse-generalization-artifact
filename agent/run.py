#!/usr/bin/env python3
"""agent/run.py — 基于 mini-swe-agent 的 LLVM InstCombine 修复主编排脚本。

用法:
    python agent/run.py                     # 处理 dataset/ 下全部 issue
    python agent/run.py 85250               # 处理指定 issue
    python agent/run.py 85250 -f            # 覆盖已有结果
    python agent/run.py 85250,76128         # 处理多个 issue（逗号分隔）

输出:
    results/agent/<model>-<issue_id>/<timestamp>/fix.json    结果 JSON（与 pipeline 格式兼容）
    results/agent/<model>-<issue_id>/<timestamp>/traj.json   mini-swe-agent 轨迹文件
    results/agent/<model>-<issue_id>/<timestamp>/preds.json  聚合预测（单次运行）

环境变量配置:
    cp agent/.env.example agent/.env   # 复制模板并填写，即可完全替代 init_agent_env.sh
    详见 env_config.py（优先级：系统环境变量 > .env > 代码默认值）
"""

import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

# 设置 sys.path
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))
sys.path.insert(0, _THIS_DIR)

# 先加载集中配置（会自动读取 agent/.env）并补齐关键环境变量，
# 避免 scripts/llvm_helper.py 在 import 时读取不到 LAB_LLVM_DIR。
from env_config import cfg

os.environ.setdefault("LAB_LLVM_DIR", cfg.llvm_dir)
os.environ.setdefault("LAB_LLVM_BUILD_DIR", cfg.llvm_build_dir)
os.environ.setdefault("LAB_DATASET_DIR", cfg.dataset_dir)
os.environ.setdefault("LAB_LLVM_ALIVE_TV", cfg.alive_tv)
os.environ.setdefault("LAB_LLVM_COST_TOOL", cfg.cost_tool)

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


def _safe_path_part(value: str) -> str:
    """将字符串转换为可用于目录名的安全片段。"""
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value)


def _build_output_paths(issue_id: str) -> tuple[str, str, str, str]:
    """构造单 issue 的输出路径。

    目录结构:
      results/agent/<model>-<issue_id>/<timestamp>/
        - fix.json
        - traj.json
        - preds.json
    """
    model_part = _safe_path_part(cfg.llm_model)
    issue_part = _safe_path_part(issue_id)
    case_dir = os.path.join(RESULTS_AGENT_DIR, f"{model_part}-{issue_part}")
    os.makedirs(case_dir, exist_ok=True)

    run_dir = os.path.join(case_dir, cfg.run_timestamp)
    os.makedirs(run_dir, exist_ok=True)

    fix_log_path = os.path.join(run_dir, "fix.json")
    traj_path = os.path.join(run_dir, "traj.json")
    preds_path = os.path.join(run_dir, "preds.json")
    return run_dir, fix_log_path, traj_path, preds_path


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


def build_task_description(env: Env, issue_id: str) -> str:
    """构造传给 agent.run(task=...) 的任务描述文本。"""
    bug_type = env.get_bug_type()
    components = list(env.get_hint_components() or [])
    component = components[0] if components else "Unknown"
    bug_funcs = env.get_hint_bug_functions() or {}

    # 获取第一个未优化的测试用例
    issue_desc = ""
    for test_file in env.get_tests():
        for test in test_file["tests"]:
            src = test.get("current_optimized_program", "").strip()
            exp = test.get("expect_optimized_program", "").strip()
            if src != exp:
                issue_desc = (
                    f"The following program reveals the missed optimization.\n"
                    f"The source program is:\n```llvm\n{test['source_program']}\n```\n\n"
                    f"The expected optimized program is:\n```llvm\n{test['expect_optimized_program']}\n```\n"
                )
                break
        if issue_desc:
            break

    desc = f"**Issue ID**: {issue_id}\n"
    desc += f"**Bug Type**: {bug_type}\n"
    desc += f"**Component**: {component}\n"

    if bug_funcs:
        for fname, funcs in bug_funcs.items():
            desc += f"**Hint — Bug Location**: {fname} → {', '.join(funcs)}\n"

    desc += f"\n{issue_desc}"

    # 如果有 localize 结果，也注入
    if cfg.localize_output and os.path.isfile(cfg.localize_output):
        try:
            with open(cfg.localize_output, "r") as f:
                localize_data = json.load(f)
            if issue_id in localize_data:
                loc = localize_data[issue_id]
                pred_funcs = loc.get("pred_funcs", {})
                if pred_funcs:
                    desc += (
                        "\n**Localization Predictions** (from pre-computed analysis):\n"
                    )
                    for fpath, fnames in pred_funcs.items():
                        desc += f"  - {fpath}: {', '.join(fnames)}\n"
        except Exception:
            pass

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
    return result


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


def fix_issue(issue_id: str, override: bool = False):
    """用 mini-swe-agent 修复单个 issue。"""
    run_output_dir, fix_log_path, traj_path, preds_path = _build_output_paths(issue_id)

    if not override and os.path.exists(fix_log_path):
        print(f"Skip {issue_id} (result exists, use -f to override)")
        return

    print(f"\n{'='*60}")
    print(f"Fixing {issue_id}")
    print(f"{'='*60}")

    start_time = time.time()

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

    # 4. 构造任务描述
    task = build_task_description(env, issue_id)

    # 5. 创建 mini-swe-agent 组件
    litellm_name = make_litellm_model_name(cfg.llm_model, cfg.llm_url)
    model_kwargs = {
        "temperature": 1.0,
        "timeout": 300,
    }
    # 为 OpenAI-compatible 端点设置 api_base 和 api_key
    if cfg.llm_url and not _is_official_openai_endpoint(cfg.llm_url):
        model_kwargs["api_base"] = cfg.llm_url
    if cfg.llm_token:
        model_kwargs["api_key"] = cfg.llm_token

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

    # 8. 转换并保存结果
    agent_messages = []
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
5. **Build & Test**: Run `cd {{cwd}} && python $AGENT_TOOLS_DIR/build_and_check.py` to verify.
6. **Iterate**: If tests fail, analyze errors and refine your patch.
7. **Regression Test**: Once fast checks pass, run `cd {{cwd}} && python $AGENT_TOOLS_DIR/check_full.py`.
8. **Submit**: When all checks pass, run `echo SUBMIT_PATCH` to finalize.

## Important Constraints

- Each bash command runs in a **fresh subshell** — always prefix with `cd {{cwd}} && `.
- Only modify files under `llvm/lib/` and `llvm/include/`.
- Make minimal changes — do not refactor unrelated code.
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

When ready, make your changes and run `cd {{cwd}} && python $AGENT_TOOLS_DIR/build_and_check.py` to verify."""


# ──────────────────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    override = False
    task_list = []

    if len(sys.argv) == 1:
        # 处理全部 issue
        task_list = sorted(
            [x.removesuffix(".json") for x in os.listdir(llvm_helper.dataset_dir)]
        )
    else:
        # 处理指定 issue（支持逗号分隔）
        ids = sys.argv[1].split(",")
        task_list = [x.strip() for x in ids if x.strip()]
        if len(sys.argv) >= 3 and sys.argv[2] == "-f":
            override = True

    print(f"Agent workflow — {len(task_list)} issue(s) to process")
    print(f"Model: {cfg.llm_model}")
    print(f"Results root: {RESULTS_AGENT_DIR}")
    print(f"Run timestamp: {cfg.run_timestamp}")
    print()

    for task_id in task_list:
        try:
            fix_issue(task_id, override=override)
        except Exception as e:
            print(f"ERROR processing {task_id}: {e}")
            import traceback

            traceback.print_exc()
