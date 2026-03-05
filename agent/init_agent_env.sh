#!/bin/bash
# agent/init_agent_env.sh — Agent 模式专用的环境初始化脚本
#
# 用法:
#   source agent/init_agent_env.sh <model_name>
#
# 示例:
#   source agent/init_agent_env.sh gpt-4.1
#   source agent/init_agent_env.sh sonnet-4.5
#
# 在 init_env.sh 基础上额外设置 agent 专用变量，
# 包括 mini-swe-agent 输出目录、step/cost 限制等。
#
# 替代方案: 复制 agent/.env.example 为 agent/.env 并填写，
# run.py 会通过 env_config.py 自动加载，无需每次 source。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 先 source 主 init_env.sh 以获取基础环境变量
source "$PROJECT_ROOT/init_env.sh" "$1"
if [ $? -ne 0 ]; then
    echo "Failed to source init_env.sh"
    return 1
fi

# ── Agent 专用配置 ──

# Agent 步数限制（每个 step = 一次 LLM 调用 + 一次 bash 执行）
export LAB_AGENT_STEP_LIMIT=${LAB_AGENT_STEP_LIMIT:-30}

# Agent 成本限制（美元）
export LAB_AGENT_COST_LIMIT=${LAB_AGENT_COST_LIMIT:-5.0}

# Agent 输出目录
export LAB_FIX_DIR_AGENT="$LAB_RESULTS_DIR/agent/fixes-$1-agent"
export LAB_TRAJ_DIR_AGENT="$LAB_RESULTS_DIR/agent/traj-$1-agent"
mkdir -p "$LAB_FIX_DIR_AGENT"
mkdir -p "$LAB_TRAJ_DIR_AGENT"

# mini-swe-agent 不需要全局配置（我们通过 Python bindings 直接传参）
# 但为了安全，设置全局成本限制
export MSWEA_GLOBAL_COST_LIMIT=${MSWEA_GLOBAL_COST_LIMIT:-10.0}
export MSWEA_GLOBAL_CALL_LIMIT=${MSWEA_GLOBAL_CALL_LIMIT:-200}

echo "########################################################"
echo "Agent environment setup complete"
echo "########################################################"
echo "Using model: $1"
echo "Step limit: $LAB_AGENT_STEP_LIMIT"
echo "Cost limit: $LAB_AGENT_COST_LIMIT"
echo "Fix directory: $LAB_FIX_DIR_AGENT"
echo "Traj directory: $LAB_TRAJ_DIR_AGENT"
echo "########################################################"
