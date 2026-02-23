# pipeline mini-swe-agent 架构设计指南

> 目标：将当前 `pipeline/` 的脚本式执行（`localize -> generate/generate_orig -> retest -> generalize`）重构为 **mini-swe-agent 驱动的可编排执行流**，同时保留与现有 `scripts/lab_env.py`、`scripts/llvm_helper.py` 的兼容性。

---

## 1. 背景与设计目标

当前流水线已经具备完整能力：

- `localize.py`：定位候选文件/函数。
- `generate.py` / `generate_orig.py`：补丁生成与迭代验证。
- `retest_patches.py`：对历史 patch 二次复测。
- `generalize.py`：从单样例扩展模式样本。

但现状偏「脚本串行调用」，存在以下问题：

1. **跨阶段状态分散**（JSON 文件、环境变量、临时目录、日志结构各自独立）。
2. **失败恢复粒度粗**（通常按脚本重跑，而不是按 stage/issue 断点续跑）。
3. **观测与治理能力不足**（阶段级指标、统一错误码、重试策略未抽象）。

因此推荐引入 mini-swe-agent 作为流程编排层，形成：

- 可声明的 stage DAG（主线 + 可选支线）；
- 统一 RunState / ArtifactStore；
- 统一 Tool API（封装 `lab_env`/`llvm_helper`）；
- 标准化恢复、重试、审计与报告。

---

## 2. 当前流程到 Agent 流程的映射

### 2.1 主线映射（必须）

```text
IssueSelector
  -> LocalizeAgent (可选但默认开启)
  -> PatchAgent (mode=normal | orig)
  -> RetestAgent
  -> ReportAgent
```

### 2.2 增强支线映射（可选）

```text
PatchAgent or RetestAgent
  -> GeneralizeAgent (按策略触发)
  -> ReportAgent
```

### 2.3 触发条件建议

- `LocalizeAgent`：默认 ON，可通过配置关闭（回退 hint bug function）。
- `PatchAgent.mode`：
  - `issue_id` 含 `-orig` 或数据集判定为 orig => `orig`；
  - 否则 `normal`。
- `GeneralizeAgent`：
  - 仅在 patch 成功或需要模式分析时触发；
  - 默认不阻塞主线完成。

---

## 3. 总体分层架构（包含 pipeline 与 scripts 边界）

## 3.1 分层

1. **Flow 层（pipeline/agent）**
   - 定义 stage、路由规则、重试策略、checkpoint。
2. **Policy 层（pipeline/agent/policies）**
   - 决策“何时 localize / 何时 generalize / 如何处理 fast-full 分歧”。
3. **Tool 层（pipeline/agent/tools）**
   - 统一调用 `scripts/lab_env.py` 与 `scripts/llvm_helper.py`。
4. **Runtime 层（scripts）**
   - 真正执行 `reset/build/check/apply/alive2/verify_lit`。
5. **Artifact 层（results + fix/retest json）**
   - 保存可恢复状态与可审计结果。

## 3.2 边界约束（关键）

- `pipeline/agent/*` **不直接写 shell 命令**，只通过 Tool 接口访问运行能力。
- 与 LLVM 仓库状态相关的写操作（reset/apply/build）必须经 `EnvTool`/`PatchTool` 统一执行。
- `scripts/*` 继续作为“运行时基座”，不强依赖 agent 框架。

---

## 4. 建议目录结构

```text
pipeline/
  mini-swe-agent-guide.md
  agent/
    flow.py                  # 主编排入口 run_issue/run_batch
    state.py                 # RunState / StageState / ArtifactIndex
    router.py                # stage 选择与转移
    checkpoints.py           # 断点保存/加载
    report.py                # 汇总报告生成
    stages/
      issue_selector.py
      localize_stage.py
      patch_stage.py
      retest_stage.py
      generalize_stage.py
    tools/
      env_tool.py            # 封装 lab_env.Environment
      llvm_tool.py           # 封装 llvm_helper
      artifact_tool.py       # 产物读写与schema校验
    prompts/
      localize_prompts.py
      patch_prompts.py
      generalize_prompts.py
    policies/
      retry_policy.py
      stage_policy.py
      gating_policy.py
```

> 说明：初期可直接复用现有 `pipeline/*.py` 的 prompt 与函数，逐步迁移到 `agent/prompts/*`。

---

## 5. 核心状态模型（RunState）

建议每个 issue 维护一个统一状态对象：

```python
RunState = {
  "run_id": str,
  "issue_id": str,
  "mode": "normal" | "orig",
  "stage": "issue_selector|localize|patch|retest|generalize|report|done|failed",
  "status": "pending|running|success|failed|skipped",
  "attempts": {
    "localize": int,
    "patch": int,
    "retest": int,
    "generalize": int,
  },
  "artifacts": {
    "localize": str | None,
    "patch": str | None,
    "retest": str | None,
    "generalize": str | None,
    "checkpoint": str | None,
  },
  "metrics": {
    "build_count": int,
    "fast_check_count": int,
    "full_check_count": int,
    "wall_time_sec": float,
  },
  "last_error": {
    "code": str,
    "message": str,
    "stage": str,
    "retryable": bool,
  } | None,
}
```

### 5.1 断点恢复建议

- stage 成功后立即持久化 checkpoint（JSON）。
- 支持 `resume_from=stage_name` 与自动恢复（读取最近一次 checkpoint）。
- 对已存在产物按 `override` 决定跳过或重算。

---

## 6. Stage 设计（输入/输出/重试/失败处理）

## 6.1 IssueSelector

- **输入**：issue_id（可空）、dataset_dir。
- **输出**：任务列表（单 issue 或 batch）。
- **策略**：
  - 兼容单 issue / 全量 / 逗号分隔；
  - 统一 normal 与 orig 任务识别。

## 6.2 LocalizeAgent

- **输入**：`issue_id` + dataset test case + LLVM root。
- **输出**：`pred_files/pred_funcs`（落盘 localize artifact）。
- **成功判定**：存在可解析预测（允许部分为空）。
- **失败策略**：
  - 模型输出不可解析时重试（低次数）；
  - 重试后仍失败则降级到 hint function 路线。

## 6.3 PatchAgent（normal/orig）

- **输入**：issue、mode、localize artifact（可空）、env config。
- **输出**：fix artifact（含 patch、check 统计、对话日志）。
- **核心循环**：`生成 -> 应用 -> check_full -> 反馈回灌`。
- **分支差异**：
  - normal：`Environment.check_full()`（verify_test_group + verify_lit）。
  - orig：`EnvironmentOrig.check_full()`（verify_test_group_orig + verify_lit）。
- **失败策略**：
  - build 失败：提取编译错误反馈继续迭代；
  - fast pass/full fail：记录 `fast_full_mismatch_patch` 并继续迭代。

## 6.4 RetestAgent

- **输入**：patch artifact 或 patch 目录、issue_id。
- **输出**：retest artifact。
- **逻辑**：
  - 仅重测 `fast_check_pass == true` 的 patch；
  - 若 `full_check_pass == false` 优先复测 `fast_full_mismatch_patch`。
- **失败策略**：
  - patch 缺失/不可应用 -> 结构化错误，标记不可重试。

## 6.5 GeneralizeAgent（可选）

- **输入**：issue 样例（当前脚本默认为首个可用样例）。
- **输出**：泛化样例 + cost/alive2 验证结果。
- **用途**：作为模式分析与回归样例扩充，不阻塞主线。

## 6.6 ReportAgent

- **输入**：所有阶段产物。
- **输出**：统一 run report（阶段结果、核心指标、失败归因、建议动作）。

---

## 7. Tool 层接口设计（对 scripts 的封装）

## 7.1 EnvTool（封装 `scripts/lab_env.py`）

建议接口：

- `create_env(issue_id, mode, basemodel_cutoff, max_build_jobs)`
- `reset(env)`
- `check_fast(env, skip_build=False)`
- `check_full(env)`
- `dump(env, payload)`
- `get_issue_metadata(env)`（bug_type/components/hints/tests）

> 对 `mode=orig` 返回 EnvironmentOrig 适配对象（行为与 `generate_orig.py` 对齐）。

## 7.2 LLVMTool（封装 `scripts/llvm_helper.py`）

建议接口：

- `ensure_llvm_clone(issue_id, base_llvm_dir)`
- `apply_patch(patch)`
- `git_show(base_commit, file)`
- `build(max_build_jobs, cmake_args)`
- `verify_group(...)` / `verify_group_orig(...)`
- `verify_lit(...)`
- `alive2_check(src, tgt, args)`
- `cost_check(src, exp, cur)`

## 7.3 ArtifactTool

建议接口：

- `load_localize(issue_id)` / `save_localize(issue_id, data)`
- `load_fix(issue_id, mode)` / `save_fix(issue_id, data, mode)`
- `load_retest(issue_id)` / `save_retest(issue_id, data)`
- `save_checkpoint(run_state)` / `load_checkpoint(run_id, issue_id)`

---

## 8. 编排流程（主线 + 恢复）

## 8.1 单 issue 主流程

```text
run_issue(issue_id, mode, opts):
  state = load_or_init_state()
  stage = state.stage

  if stage <= issue_selector: run IssueSelector
  if opts.enable_localize and stage <= localize: run LocalizeAgent
  if stage <= patch: run PatchAgent
  if stage <= retest: run RetestAgent
  if opts.enable_generalize and stage <= generalize: run GeneralizeAgent
  run ReportAgent
```

## 8.2 批量流程

- 以 issue 为最小隔离单元；
- 支持并发但必须隔离 LLVM clone/build 目录（如 `utils/llvm-<issue>`）。

## 8.3 恢复语义

- `resume=true`：从 checkpoint 的下一个未完成 stage 继续。
- `override=true`：忽略已有 artifact 强制重跑。
- `resume + override` 同时出现时以 `override` 为准。

---

## 9. 配置映射（LAB_* 到 Agent Config）

| 现有变量 | 建议配置键 | 作用 |
|---|---|---|
| `LAB_DATASET_DIR` | `dataset.dir` | 数据集路径 |
| `LAB_LLVM_DIR` | `llvm.base_dir` | LLVM 模板目录 |
| `LAB_LLVM_BUILD_DIR` | `llvm.build_root` | 构建根目录 |
| `LAB_LLVM_ALIVE_TV` | `tools.alive2_bin` | Alive2 路径 |
| `LAB_LLVM_COST_TOOL` | `tools.cost_bin` | cost tool 路径 |
| `LAB_LLM_TOKEN` | `llm.token` | 模型鉴权 |
| `LAB_LLM_URL` | `llm.base_url` | 模型服务地址 |
| `LAB_LLM_MODEL` | `llm.model` | 模型名 |
| `LAB_LLM_TEMP` | `llm.temperature` | 温度 |
| `LAB_LLM_MAX_SAMPLE_COUNT` | `patch.max_rounds` | patch 迭代上限 |
| `LAB_LOCALIZE_OUTPUT` | `artifacts.localize.path` | localize 产物路径 |
| `LAB_FIX_DIR` | `artifacts.fix.normal_dir` | normal patch 输出 |
| `LAB_FIX_DIR_ORIG` | `artifacts.fix.orig_dir` | orig patch 输出 |
| `LAB_PATCH_DIR` | `artifacts.retest.input_dir` | retest patch 输入 |
| `LAB_RETEST_DIR` | `artifacts.retest.output_dir` | retest 输出 |

---

## 10. 错误模型与重试策略

## 10.1 错误码建议

- `E_DATASET_NOT_FOUND`
- `E_ENV_MISSING`
- `E_LLM_RESPONSE_FORMAT`
- `E_BUILD_FAILED`
- `E_PATCH_APPLY_FAILED`
- `E_FAST_CHECK_FAILED`
- `E_FULL_CHECK_FAILED`
- `E_RETEST_INPUT_INVALID`
- `E_GENERALIZE_PARSE_FAILED`

## 10.2 重试建议

1. **可重试**：LLM 输出格式问题、临时构建失败、短时外部工具异常。
2. **不可重试**：环境变量缺失、数据集缺字段、patch 为空/结构错误。
3. **重试退避**：指数退避 + 上限次数（stage 粒度）。

---

## 11. 观测与报告

至少记录以下维度：

- run 维度：`run_id/issue_id/mode/status/duration`；
- stage 维度：开始/结束时间、重试次数、输入输出 artifact；
- 校验维度：`build_count/fast_check_count/full_check_count`；
- 质量维度：patch 是否通过 retest、是否出现 fast/full 分歧。

建议输出：

1. `results/agent-runs/<run_id>/<issue_id>/state.json`
2. `results/agent-runs/<run_id>/<issue_id>/report.md`
3. `results/agent-runs/<run_id>/summary.json`

---

## 12. 分阶段实施路线（MVP -> 增强）

## 阶段 A（MVP）

- 先做单 issue 主线：`localize -> patch -> retest`。
- 复用现有 `pipeline/*.py` 的关键函数，不立即重写 prompt。
- 完成 checkpoint/resume 与统一报告。

## 阶段 B（稳定化）

- 补齐批量调度、错误码、重试策略、并发隔离。
- 把 `generate.py` / `generate_orig.py` 公共逻辑收敛到同一 PatchStage。

## 阶段 C（增强）

- 接入 `generalize` 支线自动触发策略。
- 引入策略学习（哪些 issue 值得启用 localize/generalize）。

---

## 13. 最小验证清单

1. **单 issue normal**：能从 localize 到 retest 完整跑通。
2. **单 issue orig**：能走 `verify_test_group_orig` 路线并产出 fix/retest。
3. **断点恢复**：patch 前中断，恢复后从 patch 继续。
4. **fast/full 分歧**：正确记录并复测 `fast_full_mismatch_patch`。
5. **环境缺失**：给出明确错误码与可读提示，不进入无效重试。

---

## 14. 关键风险与规避

1. **字符串替换误改多处**（`str.replace`）
   - 规避：在 PatchStage 增加“替换命中数校验（=1）”，异常即失败返回。
2. **localize 输出漂移导致解析失败**
   - 规避：统一解析器 + 降级路径（hint function）。
3. **LLVM build 缓存污染**
   - 规避：issue 级 clone/build 隔离；stage 前强制 reset。
4. **orig 与 normal 判定混淆**
   - 规避：IssueSelector 中固定 mode 判定并写入 RunState，不在后续 stage 推断。

---

## 15. 建议的主入口接口

```python
def run_issue(issue_id: str, *, mode: str | None = None, resume: bool = True, override: bool = False) -> dict: ...

def run_batch(issue_ids: list[str] | None = None, *, include_orig: bool = True, concurrency: int = 1) -> dict: ...
```

返回统一结构：

```python
{
  "run_id": str,
  "issue_id": str,
  "mode": "normal|orig",
  "status": "success|failed|partial",
  "artifacts": {...},
  "metrics": {...},
  "errors": [...],
}
```

---

## 16. 结论

本设计的核心不是“替换现有脚本能力”，而是通过 mini-swe-agent 增加一层稳定编排：

- 上层统一状态与策略（可恢复、可治理）；
- 下层复用现有 `pipeline + scripts` 能力（低迁移成本）；
- 先主线 MVP，再逐步增强（风险可控，收益持续）。
