# LLVM InstCombine 修复 Agent

基于 [mini-swe-agent](https://mini-swe-agent.com/) 的 LLVM missed optimization
自动修复模块。

本目录提供一个可自主探索源码、执行定向修改并完成验证的 Agent 流程，
并与 `pipeline/` 保持一致的验证与输出格式。

---

## 1) 定位与目标

`agent/` 工作流面向 LLVM 的 **missed optimization** 问题（尤其是 InstCombine 场景）。

核心目标：

- 按 issue 独立执行自动修复
- 使用“读源码 → 改代码 → 构建验证 → 迭代”的工具闭环
- 产出与现有 pipeline 兼容的结果（可直接复测）

它**不是**通用仓库自动修复框架，而是围绕本项目 LLVM 任务做专门化设计。

---

## 2) 目录结构

```text
agent/
├── run.py                  # 主编排入口（issue 循环、环境/模型/agent 组装、结果导出）
├── run_batch.py            # 批量测试脚本（改 .env 切模型后调用 run.py）
├── llvm_agent.py           # LLVMFixAgent（安全拦截 + 自定义提交协议）
├── llvm_env.py             # LLVMEnvironment（cwd/env 注入 + 超时控制）
├── env_config.py           # 集中管理环境变量（自动加载 .env，提供 cfg 单例）
├── .env.example            # 模板——复制为 .env 并填写实际值
├── config.yaml             # Prompt/配置参考文件（当前 run.py 未直接读取）
├── init_agent_env.sh       # Agent 环境初始化（先 source 根目录 init_env.sh）
├── tools/                  # Agent 在 bash 中调用的 CLI 工具集
│   ├── _setup.py           # 工具脚本共享初始化（sys.path + 环境检查）
│   ├── issue_info.py       # 输出 issue 元信息与测试用例
│   ├── view_source.py      # 按行查看 LLVM 源码
│   ├── apply_code.py       # 代码修改（replace / write / sed）
│   ├── build_and_check.py  # 构建 + 快速验证
│   ├── check_full.py       # 构建 + 快速验证 + llvm-lit 回归
│   ├── alive2_check.py     # Alive2 语义验证
│   ├── get_langref.py      # 查询 LLVM LangRef 片段
│   └── show_diff.py        # 查看 llvm/lib 与 llvm/include 当前 diff
└── README.md / README-zh.md
```

---

## 3) 执行生命周期

每个 issue 在 `agent/run.py` 中大致经历以下流程：

1. 在 `utils/llvm-<issue_id>` 下准备 issue 独立 LLVM 克隆。
2. 初始化或复用构建缓存（`build/<issue_id>` 与 `build/<issue_id>_cache`）。
3. 基于 dataset 元数据、失败测试用例和可选定位信息构造任务描述。
4. 创建 **模型 + 环境 + Agent**：
   - 模型：`minisweagent.models.get_model(...)`
   - 环境：`LLVMEnvironment`
   - Agent：`LLVMFixAgent`
5. 启动自主循环（通过 bash action 调用 `agent/tools/`）。
6. 结束后复用 `lab_env.Environment` 做最终验证（先 fast，再 full）。
7. 导出与 pipeline 兼容的 JSON 结果与轨迹。

---

## 4) 前置依赖

- Python `>=3.10`
- 主机具备 LLVM 构建工具链（`git`、`cmake`、`ninja`、C/C++ 编译器）
- 项目依赖已安装
- 已安装 `mini-swe-agent`（代码中按 `minisweagent` 导入）

建议在仓库根目录执行：

```bash
uv sync
uv pip install mini-swe-agent
```

---

## 5) 环境配置

**只需一个文件**：复制 `agent/.env.example` → `agent/.env` 并填写即可。
可完全替代 `init_agent_env.sh` + `init_env.sh`。

```bash
cp agent/.env.example agent/.env
# 编辑 agent/.env，填入 LAB_LLM_TOKEN、LAB_LLM_MODEL、LAB_LLM_URL
python agent/run.py 85250
```

`agent/env_config.py` 在 import 时通过 `python-dotenv` 自动加载 `.env`。
优先级：**系统环境变量 > `.env` 文件 > 代码默认值**。

所有变量、含义和默认值均在 `.env.example` 中有详细注释。

### 关键环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `LAB_LLM_TOKEN` | **是** | | API 密钥 |
| `LAB_LLM_URL` | | `https://api.openai.com/v1` | API 端点 |
| `LAB_LLM_MODEL` | | `gpt-4o` | 模型名（自建端点会按原样透传，支持有前缀或无前缀；请填写网关实际注册名） |
| `LAB_AGENT_LOCALIZE_MODE` | | `pipeline` | 运行时定位模式兜底（`pipeline` 或 `lite`），可被 CLI `--localize-mode` 覆盖 |
| `LAB_AGENT_RUNTIME_LOCALIZE` | | `ON` | 当 `results/agent/<model>/<issue>/localization.json` 缺失时，启用运行时定位 |
| `LAB_AGENT_STEP_LIMIT` | | `30` | 每个 issue 最大步数 |
| `LAB_AGENT_COST_LIMIT` | | `5.0` | 每个 issue 最大成本（美元） |
| `MSWEA_GLOBAL_COST_LIMIT` | | `10.0` | mini-swe-agent 全局成本上限 |
| `MSWEA_GLOBAL_CALL_LIMIT` | | `200` | mini-swe-agent 全局调用上限 |
| `LAB_LLVM_DIR` | | `utils/llvm/llvm-project` | 基础 LLVM 源码（per-issue 克隆的种子仓库） |
| `LAB_LLVM_BUILD_DIR` | | `build/` | 构建根目录 |
| `LAB_LLVM_ALIVE_TV` | | `utils/alive2/build/alive-tv` | Alive2 工具路径 |
| `LAB_LLVM_COST_TOOL` | | `utils/cost/cost` | 代价分析工具路径 |
| `LAB_FIX_DIR_AGENT` | | 根据模型名自动生成 | 结果 JSON 输出目录 |
| `LAB_TRAJ_DIR_AGENT` | | 根据模型名自动生成 | 轨迹 JSON 输出目录 |

> Agent 运行时会固定使用 `<project_root>/dataset`（与
> `pipeline/generate.py` 保持一致）。外部传入的 `LAB_DATASET_DIR` 不再覆盖。

> **历史方式**: `source agent/init_agent_env.sh <model>` 仍可使用，但不再必需。

---

## 6) 运行方式

```bash
# 单个 issue
python agent/run.py 85250

# 多个 issue（逗号分隔）
python agent/run.py 85250,76128

# 跑 dataset 目录下全部 issue
python agent/run.py

# 覆盖已有结果
python agent/run.py 85250 -f

# 强制运行时定位模式
python agent/run.py 85250 --localize-mode pipeline
python agent/run.py 85250 --localize-mode lite
```

定位模式优先级：

- CLI `--localize-mode` > 环境变量 `LAB_AGENT_LOCALIZE_MODE` > 默认值 `pipeline`。
- 每个 issue 的定位缓存路径固定为：`results/agent/<safe_model>/<safe_issue>/localization.json`。
- 若该文件存在则优先使用；若缺失则执行 runtime localization，并持久化回同一路径。

### 批量测试脚本（多模型 × 同一组用例）

`agent/run_batch.py` 支持将同一组 issue 依次在多个模型上执行，并严格保持你传入的模型顺序。

模型选择会在每轮通过 `run.py --model` 直接传递。
`run_batch.py` **不会** 改写 `agent/.env`。

```bash
# 1) 两个模型（按顺序），单个 issue
uv run python agent/run_batch.py \
  --model openai/gpt-5.3-codex \
  --model qwen/qwen3.5-plus \
  --issues 104772

# 2) 单模型，多个 issue
uv run python agent/run_batch.py \
  --model openai/gpt-5.3-codex \
  --issues 104772,85250

# 3) 多模型，全部 issue
uv run python agent/run_batch.py \
  --models openai/gpt-5.3-codex,qwen/qwen3.5-plus \
  --issues all

# 4) 覆盖已有结果（-f 透传给 run.py）
uv run python agent/run_batch.py \
  --model openai/gpt-5.3-codex \
  --issues 104772 \
  -f

# 5) 在批量模式中强制使用 pipeline 运行时定位
uv run python agent/run_batch.py \
  --model qwen3.5-plus \
  --issues all \
  --localize-mode pipeline \
  --thinking-profile '{"enable_thinking":true,"thinking_budget":81920}' \
  -f \
  --retest \
  --retest-force
```

主要参数：

- `--model <name>`：可重复传入，按输入顺序执行。
- `--models a,b,c`：逗号分隔模型列表。
- `--issues all|<id>|<id1,id2,...>`：单个/多个/全部 issue（对所有模型生效）。
- `--localize-mode pipeline|lite`：向 `run.py` 透传运行时 localization 模式。
- `-f/--force`：向 `run.py` 透传 `-f`。
- `--stop-on-error`：某个模型失败时立即停止（默认继续后续模型）。

若使用自建端点（`LAB_LLM_URL` 非 OpenAI 官方域名），`run.py` 会按原样
使用 `LAB_LLM_MODEL`（支持有前缀或无前缀）。请填写网关实际注册名。

补充说明：

- 未传 issue id 时，会处理 dataset 下全部 `*.json`。
- 非 verified 的 issue 会被跳过。
- 若某 issue 在当前基线上已通过 fast check，也会被跳过。

---

## 7) 输出说明

主输出（按 issue 分目录、带时间戳）：

- `results/agent/<model>-<issue_id>/<timestamp>/fix.json`
- `results/agent/<model>-<issue_id>/<timestamp>/traj.json`
- `results/agent/<model>-<issue_id>/<timestamp>/preds.json`

Pipeline 兼容扁平输出（可直接被 `pipeline/retest_patches.py` 消费）：

- `results/agent/fixes-<model>-agent-<timestamp>/<issue_id>.json`
- `results/agent/traj-<model>-agent-<timestamp>/<issue_id>.traj.json`

结果文件与 pipeline 消费方（尤其 `pipeline/retest_patches.py`）兼容。

常见关键字段：

- `wall_time`
- `build_count`, `build_failure_count`
- `fast_check_count`, `full_check_count`
- `fast_check_pass`, `full_check_pass`
- `patch`
- `log.model`, `log.messages`, `log.trajectory`
- `check_fast_log`, `check_full_log`

### 历史补丁复测（直接沿用 pipeline）

补丁可复现性复测统一使用 **pipeline 脚本**（不在 agent 侧重复实现 retest 逻辑）：

```bash
LAB_PATCH_DIR=<results/agent/fixes-...-<timestamp>> \
python pipeline/retest_patches.py [issue_id_or_csv] [-f]
```

`pipeline/retest_patches.py` 会读取 `dataset/<issue_id>.json` 的 **`dev_tests`**
（developer golden test cases）进行验证。该集合与常规修复环节使用的 `tests`
不同，二者不要混用。

---

## 8) 工具脚本说明（`agent/tools/`）

这些脚本主要由 Agent 自动调用，也适合人工调试时单独使用。

| 工具 | 作用 |
|---|---|
| `issue_info.py <issue_id>` | 输出 issue 元信息、组件与失败测试 |
| `view_source.py <file> [start] [end]` | 带行号查看 LLVM 源码 |
| `apply_code.py replace/write/sed ...` | 以定向方式修改源码 |
| `build_and_check.py` | 构建并执行快速验证 |
| `check_full.py` | 执行完整验证（build + fast + lit） |
| `alive2_check.py <src.ll> <tgt.ll>` | 使用 Alive2 做语义等价检查 |
| `get_langref.py <instruction...>` | 查询 LangRef 相关片段 |
| `show_diff.py` | 查看当前改动 diff（`llvm/lib`、`llvm/include`） |

---

## 9) 安全与约束

`LLVMFixAgent` 在执行 action 前增加了安全策略：

- 拦截危险命令模式（例如高风险删除命令）
- 识别提交信号 `echo SUBMIT_PATCH` 并主动结束本轮运行

运行约束：

- 命令在独立 shell 上下文执行（需显式指定工作目录）
- `LLVMEnvironment` 将单次执行超时设置为 600 秒
- 预期修改范围为 `llvm/lib/` 与 `llvm/include/`

---

## 10) 与 `pipeline/` 的关系

Agent 路径复用了 `scripts/lab_env.py` 与 `scripts/llvm_helper.py` 的核心逻辑，
确保构建与验证语义和 pipeline 保持一致。

| 维度 | Pipeline（`pipeline/generate.py`） | Agent（`agent/run.py`） |
|---|---|---|
| 控制流 | 脚本固定阶段 | 自主迭代循环 |
| 修改策略 | 候选补丁生成偏“整段” | 命令驱动、增量修改 |
| 反馈机制 | 结构化轮次（`max_sample_count`） | 灵活步进（`step_limit`） |
| 输出格式 | JSON cert/log | 兼容的 JSON cert/log |

---

## 11) 常见问题（Troubleshooting）

1. **`ModuleNotFoundError: minisweagent`**
   - 需要单独安装：`uv pip install mini-swe-agent`。

2. **提示缺少环境变量**
   - 复制 `agent/.env.example` → `agent/.env`，至少填写 `LAB_LLM_TOKEN`。

3. **Issue 被跳过（unverified）**
   - 默认只处理 dataset 中 `verified=true` 的条目。

4. **Issue 被跳过（fast check 已通过）**
   - 表示当前基线已满足该 issue 的 fast-check 条件。

5. **结果已存在导致跳过**
   - 使用 `-f` 覆盖已有 `fixes-.../<issue_id>.json`。

---

## 12) 关于 `config.yaml` 的说明

`agent/config.yaml` 记录的是“期望的 prompt/config 结构参考”，但当前运行时
实际使用的是 `run.py` 中的 `_load_system_template()` 和 `_load_instance_template()`。

如果你希望 prompt 变更立刻生效，请直接修改 `run.py`。
