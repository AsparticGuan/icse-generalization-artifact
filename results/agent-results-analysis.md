# auto-opt `results/agent` 结果分析报告（2026-03-04 批次）

## 1) 分析范围

本报告基于目录 `results/agent/` 下当前可见的 7 组运行结果：

- 模型：`deepseek-chat`、`deepseek-reasoner`、`openai-gpt-5.3-codex`、`qwen3.5-plus`
- issue：`104772`、`85250`
- 每组运行目录都包含三类 JSON：`fix.json`、`traj.json`、`preds.json`

运行清单（按目录）：

| case | timestamp |
|---|---|
| deepseek-chat-104772 | 20260304-235900 |
| deepseek-chat-85250 | 20260304-235900 |
| deepseek-reasoner-104772 | 20260304-232009 |
| deepseek-reasoner-85250 | 20260304-232009 |
| openai-gpt-5.3-codex-104772 | 20260304-174702 |
| qwen3.5-plus-104772 | 20260304-210143 |
| qwen3.5-plus-85250 | 20260304-231019 |

---

## 2) 每种 JSON 文件的结构与作用

> 结论先说：
> - **`fix.json`**：面向评测与复检的“结果证书”（核心判定文件）
> - **`traj.json`**：面向复盘与审计的“完整轨迹”（最详细）
> - **`preds.json`**：面向聚合消费的“最小预测输出”（最轻量）

### 2.1 `fix.json`

#### 作用

`fix.json` 是单次 run 最核心的结果文件，记录最终 patch、检查结果、状态与日志。它是和 pipeline 兼容的统一格式，适合做后续统计、复测与对比。

#### 顶层结构（字段）

```json
{
  "wall_time": 380.447,
  "knowledge": [["base_model", "1970-12-31+0000"]],
  "build_count": 2,
  "build_failure_count": 0,
  "fast_check_count": 1,
  "full_check_count": 1,
  "fast_check_pass": true,
  "full_check_pass": true,
  "patch": "diff ...",
  "exit_status": "submitted",
  "submission": "echo SUBMIT_PATCH",
  "log": {
    "model": "custom_openai/qwen3.5-plus",
    "messages": [ ... ],
    "trajectory": ".../traj.json"
  },
  "check_fast_log": [ ... ],
  "check_full_log": [ ... ]
}
```

#### 重点字段解释

- `wall_time`：本次任务总耗时（秒）
- `build_count` / `build_failure_count`：构建次数 / 构建失败次数
- `fast_check_pass` / `full_check_pass`：快检 / 全检是否通过
- `patch`：最终 git diff（核心产物）
- `exit_status`：agent 退出状态（如 `submitted`、`LimitsExceeded`）
- `submission`：提交动作（如 `echo SUBMIT_PATCH`，为空表示未提交）
- `log.messages`：完整对话与工具调用历史
- `check_fast_log`：快检明细（本批次中通常是“测试项列表的列表”）
- `check_full_log`：全检输出摘要（本批次通常是 llvm-lit 统计字符串）

#### `check_fast_log` 子结构（本批次观察到）

`check_fast_log` 的典型形态是：

- 外层 list（按 fast check 次数）
- 每次 fast check 是一个 list（测试样例集合）
- 每个样例是 dict，字段通常包括：
  - `file`, `args`, `name`, `body`, `result`
  - `log`（其中有 `alive2`, `cost`, `filecheck`, `source_program`, `expect_optimized_program`, `current_optimized_program`, `opt_stderr`）

这部分对“为什么 pass/fail”非常有价值。

---

### 2.2 `traj.json`

#### 作用

`traj.json` 是 mini-swe-agent 轨迹原始记录，包含 agent 配置、模型配置、运行环境、消息历史、工具调用回包等，适合做**完整复盘**和**行为分析**。

#### 顶层结构（本批次）

```json
{
  "info": {
    "model_stats": {"instance_cost": 0.0, "api_calls": 19},
    "config": { ... },
    "mini_version": "2.2.6",
    "exit_status": "submitted",
    "submission": "echo SUBMIT_PATCH"
  },
  "messages": [ ... ],
  "trajectory_format": "mini-swe-agent-1.1"
}
```

#### 重点字段解释

- `info.model_stats.api_calls`：API 调用次数（横向比较很有用）
- `info.config`：完整运行配置（step_limit、cost_limit、model、env 等）
- `info.exit_status` / `info.submission`：与 `fix.json` 对齐的终态信息
- `messages`：系统、用户、assistant、tool 交互全过程

#### 安全提醒

本批次 `traj.json` 中可见 `model_kwargs.api_key`，属于敏感信息。若需要共享轨迹，建议脱敏后再分发。

---

### 2.3 `preds.json`

#### 作用

`preds.json` 是轻量聚合文件，用于按 issue 输出“模型最终 patch”，方便下游批量消费。

#### 结构

```json
{
  "104772": {
    "model_name_or_path": "custom_openai/qwen3.5-plus",
    "instance_id": "104772",
    "model_patch": "diff ..."
  }
}
```

#### 字段说明

- 顶层 key：`issue_id`
- `model_name_or_path`：模型名
- `instance_id`：issue id
- `model_patch`：最终 patch 内容

---

## 3) 各模型表现差异

## 3.1 总体对比（按模型）

| 模型 | 运行数 | fast 通过 | full 通过 | submitted | 平均耗时(s) | 平均 API 调用 | 平均消息数 | 平均 build_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| deepseek-chat | 2 | 1/2 | 1/2 | 0/2 | 753.0 | 30.0 | 63.0 | 1.5 |
| deepseek-reasoner | 2 | 0/2 | 0/2 | 0/2 | 1165.1 | 30.0 | 63.0 | 1.0 |
| openai-gpt-5.3-codex | 1 | 1/1 | 1/1 | 0/1 | 520.0 | 30.0 | 61.0 | 2.0 |
| qwen3.5-plus | 2 | 2/2 | 2/2 | 2/2 | 377.2 | 21.5 | 45.0 | 2.0 |

**解读：**

1. **qwen3.5-plus 最稳定**：两条 issue 全部 fast/full 通过，且都 `submitted`。
2. **openai-gpt-5.3-codex 验证能力强但未提交**：104772 通过 fast/full，但退出状态仍是 `LimitsExceeded`。
3. **deepseek-chat 有波动**：85250 通过，104772 失败且出现构建失败（`build_failure_count=1`）。
4. **deepseek-reasoner 本批次最弱**：两条均未通过 fast/full，耗时最长。

---

## 3.2 分 issue 对比

### Issue 104772

| 模型 | fast/full | exit_status | submission | wall_time(s) | api_calls | messages |
|---|---|---|---|---:|---:|---:|
| deepseek-chat | False / False | LimitsExceeded | 否 | 844.6 | 30 | 63 |
| deepseek-reasoner | False / False | LimitsExceeded | 否 | 1423.0 | 30 | 63 |
| openai-gpt-5.3-codex | True / True | LimitsExceeded | 否 | 520.0 | 30 | 61 |
| qwen3.5-plus | True / True | submitted | 是 | 380.4 | 19 | 40 |

### Issue 85250

| 模型 | fast/full | exit_status | submission | wall_time(s) | api_calls | messages |
|---|---|---|---|---:|---:|---:|
| deepseek-chat | True / True | LimitsExceeded | 否 | 661.4 | 30 | 63 |
| deepseek-reasoner | False / False | LimitsExceeded | 否 | 907.2 | 30 | 63 |
| openai-gpt-5.3-codex | N/A | N/A | N/A | N/A | N/A | N/A |
| qwen3.5-plus | True / True | submitted | 是 | 373.9 | 24 | 50 |

**解读：**

- 104772 是区分度更高的难例：仅 qwen 与 codex 通过 fast/full，其中仅 qwen 达到 submitted。
- 85250 上 qwen 与 deepseek-chat 都可过 fast/full，但 deepseek-chat 未触发最终提交。

---

## 4) 结论与建议

### 4.1 当前批次结论

- 若目标是“稳定通过 + 真正提交”，**qwen3.5-plus 是当前最优选择**。
- codex 在“生成可过检 patch”上表现不错，但“提交闭环”存在断点（pass 但 `LimitsExceeded`）。
- deepseek-reasoner 在当前配置（`step_limit=30`）下不理想，可能需要更长步数或更强约束提示。

### 4.2 工程建议

1. **把 `exit_status=submitted` 作为主指标**，`fast/full pass` 作为次指标。
2. 对 “pass 但未提交” 的模型（如 codex、deepseek-chat）可尝试：
   - 放宽 step_limit；
   - 增加“检测到 full pass 后立刻 SUBMIT”规则；
   - 加入终止前自检动作。
3. `traj.json` 分享前请做脱敏（特别是 API key）。

---

## 5) 文件体积观察（便于后续归档策略）

| case | fix.json(KB) | preds.json(KB) | traj.json(KB) |
|---|---:|---:|---:|
| deepseek-chat-104772 | 311.4 | 2.3 | 309.9 |
| deepseek-chat-85250 | 532.9 | 2.0 | 482.4 |
| deepseek-reasoner-104772 | 412.2 | 1.4 | 410.9 |
| deepseek-reasoner-85250 | 447.9 | 1.3 | 446.8 |
| openai-gpt-5.3-codex-104772 | 560.1 | 1.7 | 516.6 |
| qwen3.5-plus-104772 | 333.5 | 1.4 | 293.9 |
| qwen3.5-plus-85250 | 380.4 | 1.6 | 331.0 |

`preds.json` 始终很小，适合做汇总；`fix.json`/`traj.json` 由于含完整消息与测试日志，体积较大，建议按需压缩存档。

---

## 6) JSON 结构来源（代码侧）

- 输出目录与三文件命名：`agent/run.py` 中 `_build_output_paths()`
- `fix.json` 字段构造：`agent/run.py` 中 `convert_to_pipeline_format()`
- `preds.json` 聚合格式：`agent/run.py` 中 `_update_preds_json()`

（上面三处定义可以视为结构真源，运行结果文件是其落盘实例。）
