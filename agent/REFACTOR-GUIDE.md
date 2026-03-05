# 从 Pipeline 到 Agent：改造全解析

> 本文档详细解释如何将原始的 `pipeline/generate.py` 三阶段管线改造为基于 [mini-swe-agent](https://mini-swe-agent.com/) 框架的自主 Agent 工作流，包括设计动机、架构对比、逐模块映射、关键差异以及代码复用策略。

---

## 目录

1. [总览：为什么要改造](#1-总览为什么要改造)
2. [架构对比：Pipeline vs Agent](#2-架构对比pipeline-vs-agent)
3. [核心概念映射](#3-核心概念映射)
4. [逐模块改造详解](#4-逐模块改造详解)
   - 4.1 [LLM 交互层](#41-llm-交互层)
   - 4.2 [工具系统](#42-工具系统)
   - 4.3 [执行环境](#43-执行环境)
   - 4.4 [控制流 / 迭代循环](#44-控制流--迭代循环)
   - 4.5 [Prompt 设计](#45-prompt-设计)
   - 4.6 [结果收集与格式兼容](#46-结果收集与格式兼容)
5. [关键代码对照](#5-关键代码对照)
6. [代码复用清单](#6-代码复用清单)
7. [新增概念与机制](#7-新增概念与机制)
8. [文件结构对照](#8-文件结构对照)
9. [数据流对比图](#9-数据流对比图)
10. [FAQ](#10-faq)

---

## 1. 总览：为什么要改造

### Pipeline 的局限

原始 `pipeline/generate.py` 采用**脚本编排 + 固定循环**的模式：

```
localize.py → generate.py → retest_patches.py
                  │
                  ├── 提取 hunk（函数代码片段）
                  ├── 构造 prompt（"修改这段代码"）
                  ├── LLM 生成完整替换代码
                  ├── modify_inplace() 写回文件
                  ├── check_full() 验证
                  └── 失败则构造 feedback → 重试（最多 max_sample_count 轮）
```

这种模式有几个问题：

| 问题 | 表现 |
|------|------|
| **刚性控制流** | 必须先 localize 出函数 → 再生成替换代码 → 固定反馈格式，LLM 没有自主探索能力 |
| **信息瓶颈** | LLM 只能看到预提取的函数代码片段（hunk），无法自主浏览其他文件/函数 |
| **反馈模板化** | build failure / test failure 的反馈格式是硬编码的，LLM 无法主动获取更多上下文 |
| **工具有限** | 只有 `get_source`（10 行）、`get_instruction_docs`、`check_refinement` 三个工具 |
| **迭代次数少** | `max_sample_count` 默认 4 轮，每轮只做一次"生成+验证" |

### Agent 的优势

改为 Agent 后，LLM 获得了**自主决策权**：

```
Agent 循环：
  while not done:
      LLM 决定下一步 bash 命令 →
      环境执行并返回结果 →
      LLM 观察结果，决定下一步
```

- LLM 可以自主 `grep` 搜索代码、阅读多个文件、尝试多种修改策略
- 工具数量从 3 个扩展到 8+ 个 CLI 脚本 + 所有标准 bash 命令
- 反馈不再模板化，LLM 直接阅读编译器报错和测试输出
- 步数从 4 轮扩展到 30 步（每步可执行任意 bash 命令）

---

## 2. 架构对比：Pipeline vs Agent

### Pipeline 架构

```
┌─────────────────────────────────────────────────────┐
│                  generate.py (脚本)                   │
│                                                       │
│  ┌──────────┐    ┌──────────┐    ┌────────────────┐  │
│  │ OpenAI   │    │ 3 个      │    │ lab_env.py     │  │
│  │ Client   │───▶│ tool 函数 │───▶│ llvm_helper.py │  │
│  │ (直连)   │    │ (Python)  │    │ (直接调用)     │  │
│  └──────────┘    └──────────┘    └────────────────┘  │
│       │                                    │          │
│       ▼                                    ▼          │
│  messages[]                         modify_inplace()  │
│  full_messages[]                    check_full()      │
│       │                                    │          │
│       ▼                                    ▼          │
│  issue_fixing_iter() × max_sample_count               │
│       │                                               │
│       ▼                                               │
│  env.dump() → JSON                                    │
└─────────────────────────────────────────────────────┘
```

**特征**：
- LLM 客户端 = `openai.OpenAI` 直连
- 工具 = Python 函数，通过 OpenAI function calling 协议调用
- 控制流 = `for idx in range(max_sample_count)` 硬编码循环
- 代码修改 = `modify_inplace(file, old_hunk, llm_reply)` 整体替换
- 消息管理 = 手动维护 `messages[]` 和 `full_messages[]`

### Agent 架构

```
┌─────────────────────────────────────────────────────┐
│                  run.py (编排)                        │
│                                                       │
│  ┌──────────────┐  ┌───────────────┐  ┌───────────┐ │
│  │ LLVMFixAgent │  │ LLVMEnvironment│  │ get_model │ │
│  │ (DefaultAgent│──│ (LocalEnv      │──│ (litellm) │ │
│  │  子类)       │  │  子类)         │  │           │ │
│  └──────┬───────┘  └───────┬───────┘  └───────────┘ │
│         │                  │                          │
│         │    execute_actions()                        │
│         │         │                                   │
│         │         ▼                                   │
│         │  ┌──────────────────────┐                  │
│         │  │  subprocess.run()    │                  │
│         │  │  (bash in subshell)  │                  │
│         │  │       │              │                  │
│         │  │       ▼              │                  │
│         │  │  tools/*.py          │                  │
│         │  │  grep / cat / sed... │                  │
│         │  └──────────────────────┘                  │
│         │                                             │
│         ▼                                             │
│  agent.run(task) — 内部 step() 循环                   │
│         │                                             │
│         ▼                                             │
│  convert_to_pipeline_format() → JSON                 │
│  trajectory → .traj.json                              │
└─────────────────────────────────────────────────────┘
```

**特征**：
- LLM 客户端 = mini-swe-agent 的 `LitellmModel`（封装了 litellm）
- 工具 = bash 命令（调用 `tools/*.py` CLI 脚本 + 任意 bash）
- 控制流 = `agent.run()` → 内部 `step()` 循环，直到提交或达到限制
- 代码修改 = LLM 自主决定用 `apply_code.py` 或 `sed` 或 heredoc
- 消息管理 = mini-swe-agent 自动维护，自动保存轨迹

---

## 3. 核心概念映射

下表展示 Pipeline 中的每个核心概念在 Agent 中的对应物：

| Pipeline 概念 | Pipeline 位置 | Agent 对应 | Agent 位置 |
|---------------|---------------|------------|------------|
| OpenAI Client | `generate.py:44` `client = OpenAI(...)` | `get_model()` → `LitellmModel` | `run.py:302` |
| `chat()` / `chat_with_tooling()` | `generate.py:213-338` | `agent.query()` (自动) | mini-swe-agent 内部 |
| `messages[]` | `generate.py:1157` | `agent.messages` (自动管理) | mini-swe-agent 内部 |
| `tool_get_source` | `generate.py:84-94` | `tools/view_source.py` | CLI 脚本 |
| `tool_get_instruction_docs` | `generate.py:119-121` | `tools/get_langref.py` | CLI 脚本 |
| `tool_check_refinement` | `generate.py:157-169` | `tools/alive2_check.py` | CLI 脚本 |
| `dispatch_tool_call()` | `generate.py:192-201` | `subprocess.run()` (环境执行 bash) | mini-swe-agent 内部 |
| `modify_inplace()` | `generate.py:795-802` | `tools/apply_code.py` | CLI 脚本 |
| `env.check_fast()` | `generate.py` 中直接调用 | `tools/build_and_check.py` | CLI 脚本 |
| `env.check_full()` | `generate.py` 中直接调用 | `tools/check_full.py` | CLI 脚本 |
| `issue_fixing_iter()` 反馈构造 | `generate.py:999-1130` | LLM 自主解读 tool 输出 | 无硬编码 |
| `format_requirement` | `generate.py:341-386` | `system_template` 中的 Workflow 指导 | `run.py:420-462` |
| `get_system_prompt()` | `generate.py:388-395` | `_load_system_template()` | `run.py:418-462` |
| `issue_desc` 构造 | `generate.py:1171-1191` | `build_task_description()` | `run.py:96-145` |
| `env.dump()` | `lab_env.py:151-172` | `convert_to_pipeline_format()` | `run.py:177-217` |
| `fix_log_path` JSON | `generate.py:1140` | `fix_dir/<id>.json` | `run.py:243` |
| 无 | — | `.traj.json` 轨迹文件 | `run.py:244` |
| `ensure_llvm_clone_for_issue()` | `generate.py:447-460` | `ensure_llvm_clone_for_issue()` | `run.py:79-93` (复用) |
| 构建缓存逻辑 | `generate.py:1196-1208` | `setup_build_cache()` | `run.py:148-174` |
| `Env()` (lab_env) | `generate.py:1148` | 仍然使用 `Env()` 做最终验证 | `run.py:262, 372` |
| `max_sample_count` (4) | `generate.py:50` | `step_limit` (30) | `run.py:52` |

---

## 4. 逐模块改造详解

### 4.1 LLM 交互层

#### Pipeline（直连 OpenAI）

```python
# generate.py:44
client = OpenAI(api_key=token, base_url=url)

# generate.py:218
response = client.chat.completions.create(
    model=model,
    messages=messages,
    timeout=300,
    temperature=temperature,
    tools=get_available_tools(),
).choices[0].message
```

Pipeline 直接使用 `openai` SDK，手动管理 `messages` 列表，手动处理 `tool_calls`，手动拼接 `reasoning_content`。

#### Agent（litellm via mini-swe-agent）

```python
# run.py:302
model = get_model(
    input_model_name=litellm_name,
    config={
        "model_kwargs": model_kwargs,
        "observation_template": "...",
        "format_error_template": "...",
    },
)
```

Agent 使用 `get_model()` 创建 `LitellmModel` 实例。所有 LLM 调用由 mini-swe-agent 的 `agent.query()` 自动处理：
- 消息列表自动维护
- tool calls 自动解析为 bash 命令
- 格式错误自动通过 `format_error_template` 纠正
- 输出过长自动通过 `observation_template` 截断

**关键差异**：
| 维度 | Pipeline | Agent |
|------|----------|-------|
| SDK | `openai` 直连 | `litellm`（支持多供应商） |
| 消息管理 | 手动 `append_message()` | 自动 |
| 工具协议 | OpenAI function calling | bash tool calling (V2) |
| 错误处理 | 手动 try/except | `FormatError` 异常 + 自动重试 |
| 流式支持 | `chat_with_streaming()` | litellm 内部处理 |

### 4.2 工具系统

这是改造中**变化最大**的部分。

#### Pipeline：Python 函数 + OpenAI Function Calling

```python
# generate.py:84-94 — 典型工具定义
tool_get_source_desc = {
    "type": "function",
    "function": {
        "name": "get_source",
        "description": "Get the first 10 lines of the source code...",
        "parameters": { ... },
    },
}

def tool_get_source(env, args):
    file = args["file"]
    lineno = int(args["lineno"])
    path = os.path.join(llvm_helper.llvm_dir, file)
    with open(path) as f:
        source = f.readlines()
    return "```cpp\n" + "".join(source[lineno-1:lineno+9]) + "```\n"

# 分发
def dispatch_tool_call(env, name, args):
    args = json.loads(args)
    for tool in tools:
        if tool[1]["function"]["name"] == name:
            return tool[2](env, args)
```

Pipeline 的工具是 **Python 函数**，通过 OpenAI 的 function calling 协议调用。每个工具需要三元组：`(prompt_text, json_schema, python_func)`。限制：
- 只有 3 个工具（`get_source` 只看 10 行、`get_instruction_docs`、`check_refinement`）
- 工具在 Python 进程内执行，与 LLM 调用在同一控制流
- 工具参数由 OpenAI 的 function calling 结构化传递

#### Agent：CLI 脚本 + bash 命令

```python
# tools/view_source.py (独立脚本)
def main():
    file_path = sys.argv[1]
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    end = int(sys.argv[3]) if len(sys.argv) > 3 else min(start + 49, total_lines)
    # ... 输出带行号的源码
```

Agent 的工具是**独立 CLI 脚本**，通过 bash 命令调用。LLM 在 bash tool call 中写出：

```bash
cd /path/to/llvm && python tools/view_source.py llvm/lib/Transforms/InstCombine/InstCombineCompares.cpp 100 200
```

**关键差异**：

| 维度 | Pipeline 工具 | Agent 工具 |
|------|---------------|------------|
| 形式 | Python 函数 | CLI 脚本 (`tools/*.py`) |
| 调用方式 | OpenAI function calling JSON | bash 命令字符串 |
| 参数传递 | 结构化 JSON | 命令行参数 + stdin |
| 数量 | 3 个 | 8 个专用 + 所有 bash 命令 |
| 灵活性 | 低（只能调用预定义工具） | 高（可用任意 bash） |
| `view_source` | 只看 10 行 | 可指定任意行范围 |
| 代码修改 | LLM 输出完整函数 → `modify_inplace()` | `apply_code.py` 3 种模式 |
| 代码搜索 | 无 | `grep -rn` / `find` 等 |

#### 工具扩展对照表

| Agent 工具 | 对应 Pipeline 功能 | 新增能力 |
|------------|-------------------|----------|
| `issue_info.py` | `get_issue_desc()` + `get_issue_desc_from_programs()` | 输出更完整的 issue 元信息 |
| `view_source.py` | `tool_get_source()` (10 行) | 可看任意行范围，带行号 |
| `apply_code.py` | `modify_inplace()` | 3 种模式：write/sed/replace |
| `build_and_check.py` | `env.check_fast()` | 格式化输出构建错误和测试结果 |
| `check_full.py` | `env.check_full()` | 格式化输出 lit 回归测试结果 |
| `alive2_check.py` | `tool_check_refinement()` | 支持文件输入和 stdin |
| `get_langref.py` | `tool_get_instruction_docs()` | 支持多个关键字 |
| `show_diff.py` | 无 | **新增** — 查看当前修改 |
| `grep` / `find` | 无 | **新增** — 自主代码搜索 |

### 4.3 执行环境

#### Pipeline：同进程直接调用

```python
# generate.py 中直接调用 lab_env / llvm_helper
env.reset()
modify_inplace(file, src, tgt)
res, log = env.check_full()
```

Pipeline 中所有操作在同一 Python 进程内完成。`lab_env.Environment` 直接作为运行时上下文。

#### Agent：subprocess 隔离执行

```python
# llvm_env.py — 继承 LocalEnvironment
class LLVMEnvironment(LocalEnvironment):
    def __init__(self, *, issue_id, issue_llvm_dir, llvm_build_dir, ...):
        env_vars = {
            "AGENT_ISSUE_ID": issue_id,
            "LAB_LLVM_DIR": issue_llvm_dir,
            "LAB_LLVM_BUILD_DIR": llvm_build_dir,
            ...
        }
        super().__init__(cwd=issue_llvm_dir, env=env_vars, timeout=600)
```

Agent 的每个 bash 命令在**独立子进程**中执行（`subprocess.run()`），与主进程隔离。`LLVMEnvironment` 负责：
- 设置 `cwd` 为 issue 专属 LLVM 目录
- 注入所有工具脚本需要的环境变量（`AGENT_ISSUE_ID`、`LAB_LLVM_DIR`、`LAB_LLVM_BUILD_DIR` 等）
- 设置 `PYTHONPATH` 使工具脚本能 import `llvm_helper` / `lab_env`
- 超时 600 秒（LLVM 构建耗时较长）

**关键差异**：

| 维度 | Pipeline | Agent |
|------|----------|-------|
| 执行隔离 | 同进程 | subprocess 子进程 |
| 状态持久 | `cd` 有效、变量持久 | **无状态**：每次 bash 重新开始 |
| 环境变量 | 直接读 `os.environ` | `LLVMEnvironment` 注入 |
| 超时 | 无统一超时 | 600 秒 |
| 工作目录 | 隐式 | 显式 `cwd=issue_llvm_dir` |

> ⚠️ **无状态执行**是最重要的差异之一。Pipeline 中 `cd` 到某目录后后续命令自动在该目录执行；Agent 中每个 bash 命令都从 `cwd` 重新开始，因此 prompt 中反复提醒 `cd {{cwd}} && ...`。

### 4.4 控制流 / 迭代循环

这是改造中**概念变化最根本**的部分。

#### Pipeline：硬编码循环 + 固定反馈

```python
# generate.py:1232-1241 — 固定 max_sample_count 轮
for idx in range(max_sample_count):
    if issue_fixing_iter(env, file, hunk, messages, full_messages, context_requirement):
        # 成功 → 保存结果
        return

# generate.py:999-1130 — issue_fixing_iter 的反馈逻辑
def issue_fixing_iter(env, file, src, messages, full_messages, context_requirement):
    tgt = chat(env, messages, full_messages)    # LLM 生成替换代码
    modify_inplace(file, src, tgt)               # 写回文件
    res, log = env.check_full()                  # 完整验证
    if res:
        return True
    # 失败 → 根据错误类型构造固定格式的 feedback
    if build_failure:
        feedback = "Your generated code caused the following build failure:\n" + ...
    elif not fast_check_pass:
        feedback = "The test program was not successfully optimized.\n" + ...
    else:
        feedback = "Your code caused behavior change in other programs:\n" + ...
    append_message(messages, full_messages, {"role": "user", "content": feedback})
    return False
```

这里有三个固定的反馈分支（构建失败 / 快速检查失败 / lit 回归失败），模板是硬编码的。

#### Agent：自主循环 + 自由决策

```python
# run.py:355-361 — agent.run() 内部自动循环
result = agent.run(
    task=task,
    issue_id=issue_id,
    cwd=issue_llvm_dir,
    model_name=litellm_name,
)
```

`agent.run()` 内部的循环（伪代码）：

```python
# mini-swe-agent DefaultAgent.run() 内部逻辑
def run(self, task, **kwargs):
    self.messages = [system_msg, instance_msg]      # 初始化
    while True:
        message = self.query()                       # LLM 决定下一步
        observations = self.execute_actions(message) # 执行 bash
        if messages[-1].role == "exit":              # 检测终止
            break
    return self.trajectory
```

**LLM 自主决定**每一步做什么：
- 看 `build_and_check.py` 输出 → 自行判断是构建错误还是测试失败
- 决定用 `grep` 搜索更多代码、用 `view_source.py` 看更多上下文、或直接修改
- 遇到 lit 回归失败 → 自行分析 FileCheck 输出并调整

**关键差异**：

```
Pipeline:                              Agent:
┌──────────┐                           ┌──────────┐
│ Round 1   │                           │ Step 1: grep 搜索代码
│ LLM→代码  │                           │ Step 2: view_source 阅读
│ 写回→验证 │                           │ Step 3: 分析理解
│ feedback  │                           │ Step 4: apply_code 修改
├──────────┤                           │ Step 5: build_and_check
│ Round 2   │                           │ Step 6: 看到 build 错误
│ LLM→代码  │                           │ Step 7: 修正代码
│ 写回→验证 │                           │ Step 8: build_and_check ✓
│ feedback  │                           │ Step 9: check_full
├──────────┤                           │ Step 10: 看到 lit 失败
│ Round 3   │                           │ Step 11: 分析 FileCheck
│ ...       │                           │ Step 12: 调整代码
│           │                           │ Step 13: check_full ✓
├──────────┤                           │ Step 14: echo SUBMIT_PATCH
│ Round 4   │                           └──────────┘
│ 放弃      │
└──────────┘
```

| 维度 | Pipeline | Agent |
|------|----------|-------|
| 循环控制 | `for idx in range(4)` | `while not exit` (最多 30 步) |
| 每轮粒度 | 1 轮 = 完整代码生成 + 验证 | 1 步 = 1 个 bash 命令 |
| 反馈来源 | 硬编码 3 种模板 | LLM 直接读 tool 输出 |
| 决策权 | 脚本决定反馈内容 | LLM 决定下一步 |
| 终止条件 | check_full 通过 或 轮次耗尽 | `echo SUBMIT_PATCH` 或 step/cost 限制 |
| 代码修改方式 | 整体函数替换 | 任意粒度（行级/sed/patch） |

### 4.5 Prompt 设计

#### Pipeline：格式约束 + 代码模板

```python
# generate.py:388-395
def get_system_prompt():
    return (
        """You are an LLVM maintainer.
Users have reported a case where LLVM failed to optimize a specific program.
You are adding new code or modifying existing code to implement the missing optimization."""
        + format_requirement    # "请输出完整代码...用 ```cpp``` 包裹"
        + get_tooling_prompt()  # 工具使用说明
    )
```

Pipeline 的 prompt 要求 LLM 按特定格式（`### 1. Analyze` / `### 2. Propose` / `### 3. Verify` / `### 4. Submit`）输出，并在 `### 4. Submit` 中给出**完整函数替换代码**。

#### Agent：工作流指导 + 工具说明

```python
# run.py:420-462 — system template
"""You are an expert LLVM compiler maintainer...

## Available Tools (call via bash)
1. issue_info.py — View issue metadata...
2. view_source.py — View LLVM source with line numbers...
3. apply_code.py — Modify source files...
...

## Workflow
1. Understand → 2. Locate → 3. Analyze → 4. Implement → 5. Build & Test
→ 6. Iterate → 7. Regression Test → 8. Submit

## Important Constraints
- Each bash command runs in a fresh subshell...
- Only modify files under llvm/lib/ and llvm/include/...
"""
```

Agent 的 prompt 不要求特定输出格式，而是：
- 列出所有可用工具及用法
- 给出推荐的工作流程（但 LLM 可以灵活调整顺序）
- 强调无状态执行的约束（`cd {{cwd}} && ...`）

**关键差异**：

| 维度 | Pipeline | Agent |
|------|----------|-------|
| 输出格式 | 固定（`### 1-4` + `\`\`\`cpp`） | 自由（bash 命令） |
| 代码提交 | 在回复中给出完整代码 | 用 `apply_code.py` 写入文件 |
| 任务注入 | `issue_desc` + hunk 代码 | `instance_template` + `task` 文本 |
| 上下文注入 | 只有预提取的 hunk | LLM 自主获取任意上下文 |
| 工作流 | 隐式（由反馈决定） | 显式（prompt 中列出步骤） |

### 4.6 结果收集与格式兼容

#### Pipeline：env.dump()

```python
# lab_env.py:151-172
def dump(self, log=None):
    wall_time = time.time() - self.start_time - self.interaction_time_compensation
    patch = llvm_helper.git_execute(["diff", "--", "llvm/lib/*", "llvm/include/*"])
    return {
        "wall_time": wall_time,
        "knowledge": used_knowledge,
        "build_count": self.build_count,
        "build_failure_count": self.build_failure_count,
        "fast_check_count": self.fast_check_count,
        "full_check_count": self.full_check_count,
        "fast_check_pass": self.fast_check_pass,
        "full_check_pass": self.full_check_pass,
        "patch": patch,
        "log": log,
        "check_fast_log": self.check_fast_log,
        "check_full_log": self.check_full_log,
    }
```

#### Agent：convert_to_pipeline_format()

```python
# run.py:177-217
def convert_to_pipeline_format(env, agent_messages, trajectory_path, model_name, start_time):
    result = {
        "wall_time": wall_time,
        "knowledge": used_knowledge,
        "build_count": env.build_count,
        ...
        "patch": patch,
        "log": {
            "model": model_name,
            "messages": agent_messages,
            "trajectory": trajectory_path,    # 新增：轨迹文件路径
        },
        "check_fast_log": env.check_fast_log,
        "check_full_log": env.check_full_log,
    }
```

Agent 的结果 JSON **与 Pipeline 完全兼容**（同样的 key），可以直接被 `retest_patches.py` 使用。额外增加了 `trajectory` 字段指向 `.traj.json` 文件。

此外，Agent 在 `run.py` 最后会用 `lab_env.Environment` 做**独立的最终验证**（与 Pipeline 使用完全相同的验证逻辑），确保实验结果的一致性和公平性。

---

## 5. 关键代码对照

### 5.1 LLM 调用

**Pipeline**：
```python
# generate.py:213-284 — chat_with_tooling()
response = client.chat.completions.create(
    model=model, messages=messages, timeout=300,
    temperature=temperature, tools=get_available_tools()
).choices[0].message

if response.tool_calls:
    for tool_call in response.tool_calls:
        res = dispatch_tool_call(env, tool_call.function.name, tool_call.function.arguments)
        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(res)})
```

**Agent**：
```python
# agent 内部（自动）— DefaultAgent.step()
message = self.query()           # litellm 调用 → 解析 tool_calls → 生成 bash 命令
observations = self.execute_actions(message)  # subprocess 执行 bash
# messages 和 observations 自动追加到 self.messages
```

### 5.2 工具调用：查看源码

**Pipeline**：
```python
# generate.py:84-94
def tool_get_source(env, args):
    file = args["file"]
    lineno = int(args["lineno"])
    path = os.path.join(llvm_helper.llvm_dir, file)
    with open(path) as f:
        source = f.readlines()
    return "```cpp\n" + "".join(source[lineno-1:lineno+9]) + "```\n"
    # 固定只返回 10 行
```

**Agent**：
```python
# tools/view_source.py — 独立 CLI 脚本
full_path = os.path.join(llvm_helper.llvm_dir, file_path)
with open(full_path, "r") as f:
    lines = f.readlines()
start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
end = int(sys.argv[3]) if len(sys.argv) > 3 else min(start + 49, total_lines)
for i in range(start - 1, end):
    print(f"{i + 1:6d} | {lines[i]}", end="")
    # 灵活行范围，带行号
```

### 5.3 代码修改

**Pipeline**：
```python
# generate.py:795-802 — 整体替换
def modify_inplace(file, src, tgt):
    tgt = extract_code_from_reply(tgt)  # 从 LLM 回复中提取 ```cpp``` 块
    path = os.path.join(llvm_helper.llvm_dir, file)
    with open(path) as f:
        code = f.read()
    code = code.replace(src, tgt)       # 字符串替换：旧 hunk → 新代码
    with open(path, "w") as f:
        f.write(code)
```

**Agent**（LLM 自行选择方式）：
```bash
# 方式 1: 行范围替换
cat <<'CODEEOF' | python tools/apply_code.py write llvm/lib/.../file.cpp 100 120
// new code here
CODEEOF

# 方式 2: 简单字符串替换
python tools/apply_code.py sed llvm/lib/.../file.cpp 'old_line' 'new_line'

# 方式 3: 直接 sed（不用工具脚本）
sed -i 's/old_pattern/new_pattern/g' llvm/lib/.../file.cpp
```

### 5.4 验证

**Pipeline**：
```python
# generate.py:1010-1013 — 直接调用 env.check_full()
modify_inplace(file, src, tgt)
res, log = env.check_full()
if res:
    return True
```

**Agent**（LLM 自行调用）：
```bash
# LLM 决定先快速检查
cd /path/to/llvm && python tools/build_and_check.py
# 如果通过，再做完整检查
cd /path/to/llvm && python tools/check_full.py
```

### 5.5 安全与终止

**Pipeline**：
```python
# 无安全检查，check_full 通过就直接返回 True
```

**Agent**：
```python
# llvm_agent.py:36-67 — 自定义 execute_actions()
def execute_actions(self, message):
    for action in actions:
        cmd = action.get("command", "")
        # 1) 安全校验
        for pat in self.config.forbidden_patterns:
            if re.search(pat, cmd):
                raise FormatError(...)  # 拦截危险命令
        # 2) 提交检测
        if "SUBMIT_PATCH" in cmd and cmd.strip().startswith("echo"):
            raise Submitted(...)  # 终止 agent
    return super().execute_actions(message)
```

---

## 6. 代码复用清单

改造的核心原则是**最大化复用 `scripts/` 下的基础设施**，避免重复实现：

| 复用模块 | 复用方式 | 被谁调用 |
|----------|----------|----------|
| `scripts/lab_env.py` → `Environment` | 工具脚本内 import | `issue_info.py`, `build_and_check.py`, `check_full.py`, `get_langref.py` |
| `scripts/lab_env.py` → `Environment` | `run.py` 直接使用 | 最终验证 (`check_fast` + `check_full`) |
| `scripts/llvm_helper.py` → `build()` | 通过 `lab_env` 间接调用 | `build_and_check.py`, `check_full.py` |
| `scripts/llvm_helper.py` → `alive2_check()` | 工具脚本内 import | `alive2_check.py` |
| `scripts/llvm_helper.py` → `get_langref_desc()` | 工具脚本内 import | `get_langref.py` |
| `scripts/llvm_helper.py` → `git_execute()` | 工具脚本内 import | `show_diff.py`, `get_langref.py` |
| `scripts/llvm_helper.py` → `verify_test_group()` | 通过 `lab_env` 间接调用 | `build_and_check.py`, `check_full.py` |
| `scripts/llvm_helper.py` → `verify_lit()` | 通过 `lab_env` 间接调用 | `check_full.py` |
| `generate.py` → `ensure_llvm_clone_for_issue()` | 复制到 `run.py` | `run.py` |
| `generate.py` → 构建缓存逻辑 | 重构为 `setup_build_cache()` | `run.py` |
| `generate.py` → `get_issue_desc_from_programs()` | 重构为 `build_task_description()` | `run.py` |
| `lab_env.py` → `dump()` 的字段定义 | 重构为 `convert_to_pipeline_format()` | `run.py` |

---

## 7. 新增概念与机制

### 7.1 Jinja2 模板引擎

Pipeline 使用 Python f-string 和字符串拼接构造 prompt。Agent 使用 Jinja2 模板，支持：

```jinja2
# system_template 中
cd {{cwd}} && python tools/view_source.py ...
#    ^^^^^ 运行时自动替换为实际 LLVM 目录

# instance_template 中
{{task}}
#  ^^^^ 运行时替换为 build_task_description() 的输出

# observation_template 中
{% if output.output | length > 15000 %}
{{ output.output[:7500] }}
<truncated>
{{ output.output[-7500:] }}
{% endif %}
# 自动截断过长输出
```

### 7.2 轨迹文件 (.traj.json)

Pipeline 只保存 `messages[]` 到结果 JSON。Agent 额外保存 mini-swe-agent 原生轨迹文件，包含：
- 每一步的 LLM 请求和回复
- 每个 bash 命令的 stdout/stderr/returncode
- token 使用量和成本信息
- 时间戳

### 7.3 异常驱动的控制流

mini-swe-agent 使用异常来控制 agent 生命周期：

| 异常 | 触发条件 | 效果 |
|------|----------|------|
| `Submitted` | 检测到 `SUBMIT_PATCH` | 终止 agent，标记为成功提交 |
| `FormatError` | LLM 输出格式错误 / 危险命令 | 发送纠正消息，agent 继续 |
| `LimitsExceeded` | step_limit 或 cost_limit 达到 | 终止 agent |
| `TimeoutError` | bash 命令超时 (600s) | 发送超时消息，agent 继续 |

这比 Pipeline 的 `if res: return True` 更加灵活和可扩展。

### 7.4 litellm 多供应商支持

Pipeline 只支持 OpenAI-compatible API。Agent 通过 litellm 支持：
- OpenAI / Azure OpenAI
- Anthropic (Claude)
- Google (Gemini)
- 任何 OpenAI-compatible 端点

`run.py` 中的 `make_litellm_model_name()` 自动处理模型名前缀。

---

## 8. 文件结构对照

### Pipeline 结构

```
pipeline/
├── localize.py           # 阶段1：定位 bug 函数
├── generate.py           # 阶段2：LLM 生成修复（核心 1328 行）
├── retest_patches.py     # 阶段3：复测 patch
└── [其他辅助脚本]

scripts/
├── lab_env.py            # 实验环境封装
└── llvm_helper.py        # LLVM 底层工具

init_env.sh               # 环境变量初始化
```

### Agent 结构

```
agent/
├── run.py                # 主编排（对应 generate.py 的外层循环）
├── llvm_agent.py         # Agent 子类（对应 issue_fixing_iter 的控制逻辑）
├── llvm_env.py           # 环境子类（对应 env 初始化 + 变量注入）
├── config.yaml           # 配置参考（对应 get_system_prompt 等配置）
├── init_agent_env.sh     # 环境初始化（对应 init_env.sh）
├── tools/                # CLI 工具（对应 tool_get_source 等 Python 函数）
│   ├── _setup.py
│   ├── issue_info.py
│   ├── view_source.py
│   ├── apply_code.py
│   ├── build_and_check.py
│   ├── check_full.py
│   ├── alive2_check.py
│   ├── get_langref.py
│   └── show_diff.py
├── README.md
└── README-zh.md

scripts/                  # 共享 — 不变
├── lab_env.py
└── llvm_helper.py
```

### 代码量对比

| 组件 | Pipeline | Agent |
|------|----------|-------|
| 主脚本 | `generate.py` (1328 行，包含一切) | `run.py` (514 行，仅编排) |
| 控制逻辑 | 嵌入在 `generate.py` 中 | `llvm_agent.py` (68 行) |
| 环境封装 | 嵌入在 `generate.py` 中 | `llvm_env.py` (84 行) |
| 工具 | 嵌入在 `generate.py` 中 (约 180 行) | `tools/*.py` (8 个文件，约 600 行总计) |
| Prompt | 嵌入在 `generate.py` 中 | `run.py` 中的模板函数 |

Agent 总代码量更多（~1266 行），但每个文件职责单一、可独立测试。

---

## 9. 数据流对比图

### Pipeline 数据流

```
dataset/<id>.json
       │
       ▼
  Env(issue_id)  ←─── llvm_helper (全局状态)
       │
       ├──▶ get_hint_bug_functions() ─▶ hunk 提取
       │                                    │
       │     ┌─────────── prompt 构造 ◀─────┘
       │     │                 │
       │     │         ┌──────▼──────┐
       │     │         │  OpenAI API  │◀──── messages[]
       │     │         └──────┬──────┘
       │     │                │ LLM reply (完整函数代码)
       │     │                ▼
       │     │         modify_inplace()
       │     │                │
       │     │         check_full()
       │     │              │    │
       │     │         success  failure
       │     │              │    │
       │     │         dump()   构造 feedback ──▶ messages[] (下一轮)
       │     │              │
       │     ▼              ▼
       │   重试 ×4     <issue_id>.json
       │
       ▼
  结果目录: fixes-<model>/<issue_id>.json
```

### Agent 数据流

```
dataset/<id>.json
       │
       ▼
  Env(issue_id)  ←─── llvm_helper (全局状态)
       │
       ├──▶ build_task_description()
       │              │
       │              ▼ task 文本
       │     ┌────────────────────┐
       │     │  agent.run(task)   │
       │     │                    │
       │     │  ┌──────────────┐  │
       │     │  │  LitellmModel │  │ ◀──── system_template + instance_template
       │     │  └──────┬───────┘  │
       │     │         │ bash 命令  │
       │     │  ┌──────▼───────┐  │
       │     │  │LLVMEnvironment│  │
       │     │  │  subprocess   │  │
       │     │  │     │         │  │
       │     │  │  tools/*.py   │  │ ←── lab_env / llvm_helper
       │     │  │  grep/cat/... │  │
       │     │  └──────┬───────┘  │
       │     │         │ stdout    │
       │     │  ┌──────▼───────┐  │
       │     │  │ observation   │  │ ←── observation_template (截断)
       │     │  │  → messages   │  │
       │     │  └──────────────┘  │
       │     │         │           │
       │     │    step() 循环 ×30  │
       │     │         │           │
       │     │  SUBMIT_PATCH 或    │
       │     │  step_limit 到达    │
       │     └─────────┬──────────┘
       │               │
       │     ┌─────────▼─────────┐
       │     │ 最终验证 (lab_env)  │ ←── 与 Pipeline 相同的验证逻辑
       │     │ check_fast()       │
       │     │ check_full()       │
       │     └─────────┬─────────┘
       │               │
       ▼               ▼
  结果目录:
    fixes-<model>-agent/<issue_id>.json      ← 与 Pipeline 格式兼容
    traj-<model>-agent/<issue_id>.traj.json  ← 新增轨迹文件
```

---

## 10. FAQ

### Q: Agent 的结果能直接用 `retest_patches.py` 吗？
**A**: 可以。`convert_to_pipeline_format()` 输出与 `env.dump()` 相同的字段，包括 `patch`、`fast_check_pass`、`full_check_pass` 等。

### Q: Agent 跑的时候还需要先运行 `localize.py` 吗？
**A**: 不强制。Agent 可以自主定位代码（通过 `grep`、`view_source.py` 等）。但如果有预计算的 localize 结果（通过 `LAB_LOCALIZE_OUTPUT` 环境变量指定），`build_task_description()` 会将其注入 task 描述，作为辅助提示。

### Q: 为什么工具是 CLI 脚本而不是 Python 函数？
**A**: mini-swe-agent 的设计理念是「只用 bash 作为工具」。所有操作通过 bash 命令执行，这样：
1. LLM 可以自由组合工具（管道、重定向等）
2. 工具在子进程中执行，与 agent 主进程隔离
3. 可以用任意 bash 命令（`grep`、`find`、`sed`）补充工具不足

### Q: `lab_env.Environment` 和 `LLVMEnvironment` 是什么关系？
**A**: 完全不同的东西：
- `scripts/lab_env.py` 的 `Environment` = 实验数据/验证封装（读 issue JSON、调用 build/check）
- `agent/llvm_env.py` 的 `LLVMEnvironment` = mini-swe-agent 执行环境（subprocess 执行 bash）
- Agent 中两者并存：`LLVMEnvironment` 负责执行 bash，`lab_env.Environment` 负责最终验证

### Q: 无状态执行会不会有问题？
**A**: 需要注意。每个 bash 命令独立执行，`cd` 不持久。解决方案：
1. `LLVMEnvironment` 设置了 `cwd=issue_llvm_dir`，所有命令自动在 LLVM 目录执行
2. System prompt 反复提醒 `cd {{cwd}} && ...`
3. 环境变量通过 `LLVMEnvironment` 注入，工具脚本可以正常读取

### Q: 为什么还需要 `run.py` 做最终验证，而不直接信任 Agent 的 `build_and_check.py`？
**A**: 为了实验公平性。Agent 运行过程中的 `build_and_check.py` 是在 subprocess 中执行的，统计数据（build_count 等）在子进程中，不会回传。`run.py` 的最终验证使用与 Pipeline 完全相同的 `lab_env.Environment`，确保：
1. 统计指标（build_count, fast_check_pass 等）一致
2. 验证逻辑一致（同样的 `verify_test_group` + `verify_lit`）
3. 结果 JSON 格式一致

### Q: Agent 比 Pipeline 慢吗？
**A**: 每个 step 需要一次 LLM 调用 + 一次 bash 执行。30 个 step 意味着 30 次 LLM 调用（Pipeline 只有 4 次）。但 Agent 的每次调用更轻量（只返回一个 bash 命令，而不是完整函数代码），且可以更灵活地探索。总体 token 消耗和墙钟时间取决于具体场景。

---

> **总结**：改造的本质是将 Pipeline 中**脚本控制的固定循环**替换为**LLM 自主驱动的 ReAct 循环**，同时保持底层验证逻辑（`lab_env` / `llvm_helper`）不变，确保实验可比性。
