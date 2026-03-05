# FIX Logs

> 目标：测试 `agent/` 的单 issue 流程（Issue `104772`）时遇到的代码设计/实现问题修复记录。

## 1) `agent/run.py` 启动即报 `LAB_LLVM_DIR` 缺失

- **现象**
  - 执行：`uv run python agent/run.py 104772 -f`
  - 报错：`KeyError: 'LAB_LLVM_DIR'`（`scripts/llvm_helper.py` import 阶段读取环境变量失败）
- **根因**
  - `run.py` 在 import `llvm_helper` 之前，没有保证 `.env` 已加载且关键 `LAB_*` 环境变量已注入。
- **修复**
  - 在 `run.py` 顶部提前 `from env_config import cfg`。
  - 在 import `llvm_helper` 前补齐 `LAB_LLVM_DIR/LAB_LLVM_BUILD_DIR/LAB_DATASET_DIR`。

---

## 2) 后续启动报 `LAB_LLVM_ALIVE_TV` / `LAB_LLVM_COST_TOOL` 缺失

- **现象**
  - 第二次运行报：`KeyError: 'LAB_LLVM_ALIVE_TV'`。
- **根因**
  - `scripts/llvm_helper.py` import 时还会读取 `LAB_LLVM_ALIVE_TV` 和 `LAB_LLVM_COST_TOOL`。
- **修复**
  - 在 `run.py` 顶部同样提前设置：
    - `LAB_LLVM_ALIVE_TV`
    - `LAB_LLVM_COST_TOOL`

---

## 3) Alive2 路径不存在时触发异常链，导致测试流程崩溃

- **现象**
  - 运行中出现：`FileNotFoundError: .../utils/alive2/build/alive-tv`
  - 随后在 `verify_dispatch` 异常处理中又触发：`AttributeError: 'FileNotFoundError' object has no attribute 'output'`
- **根因**
  1. `alive2_check()` 仅捕获 `subprocess.CalledProcessError`，未兜底处理 `FileNotFoundError`。
  2. `verify_dispatch()` 在异常分支直接访问 `e.output/e.stderr`，对通用异常不安全。
- **修复**
  - `scripts/llvm_helper.py`：
    1. `alive2_check()` 增加通用 `except Exception` 兜底，返回失败日志而非抛出。
    2. `verify_dispatch()` 使用 `getattr(e, 'output', None)` / `getattr(e, 'stderr', None)` 安全读取。
    3. `cost_check()` 对外部工具调用异常做更稳健处理（避免流程中断）。

---

## 4) `openai` 版本与 `litellm` 不兼容

- **现象**
  - 报错：`ModuleNotFoundError: No module named 'openai.types.responses'`
- **根因**
  - 项目原先固定 `openai==1.60.1`，与当前 `litellm`（由 mini-swe-agent 使用）不兼容。
- **修复**
  - 依赖升级：
    - `pyproject.toml`: `openai>=2.8.0,<3`
    - `requirements.txt`: `openai>=2.8.0,<3`

---

## 5) 自定义模型不在 litellm 定价表时，中断 agent 主流程

- **现象**
  - 报错：`This model isn't mapped yet ... openai/qwen3.5-plus`
- **根因**
  - mini-swe-agent 默认会做 cost 估算；若模型未在 litellm price map，会抛异常中断。
- **修复**
  - `run.py` 在 `get_model` 配置中显式注入：
    - `cost_tracking = os.environ.get('MSWEA_COST_TRACKING', 'ignore_errors')`
  - 默认改为 `ignore_errors`，避免因定价表缺失阻塞主流程。

---

## 6) Prompt 中工具调用路径设计错误

- **现象**
  - agent 发起命令：`python tools/issue_info.py`（在 `utils/llvm-104772` 下执行）
  - 报错：`can't open file .../utils/llvm-104772/tools/issue_info.py`
- **根因**
  - `run.py` 的 system/instance template 假设工具位于 issue LLVM clone 的 `tools/` 下，实际工具位于 `agent/tools/`。
- **修复**
  - 在 `run.py` 的 `_load_system_template()` / `_load_instance_template()` 统一改为使用 `$AGENT_TOOLS_DIR`：
    - `python $AGENT_TOOLS_DIR/issue_info.py ...`
    - `python $AGENT_TOOLS_DIR/view_source.py ...`
    - `python $AGENT_TOOLS_DIR/build_and_check.py ...` 等。

---

## 结论

本轮针对测试暴露的问题，已完成 **6 处关键修复**（启动环境、异常健壮性、依赖兼容、cost 追踪策略、工具路径设计）。

---

## 追加测试（默认 step_limit）后的修复结论

- 执行：`env -u LAB_AGENT_STEP_LIMIT uv run python agent/run.py 104772 -f`
- 结果：流程完整执行，`fast_check/full_check` 均通过。
- 本轮 **未发现新的代码设计或实现缺陷**，因此无需新增代码修复。
