# Agent LLM 推理（思考）Level 配置指南

> 适用目录：`agent/`  
> 目的：结合当前 `agent` 代码调用链，说明不同 LLM 在不同推理/思考 level 下应如何配置。

---

## 1. 先看当前 `agent` 的 LLM 调用链（代码事实）

当前仓库里，`agent` 对 LLM 的调用路径是：

1. `agent/env_config.py` 从环境变量读取模型配置：
   - `LAB_LLM_MODEL`
   - `LAB_LLM_URL`
   - `LAB_LLM_TOKEN`
   - `LAB_LLM_TEMP`
2. `agent/run.py` 在 `fix_issue()` 里构造 `model_kwargs`，并传给 `minisweagent.models.get_model(...)`。
3. `agent/run.py` 通过 `make_litellm_model_name()` 对模型名做路由兼容（OpenAI 官方端点 vs 自建 OpenAI-compatible 端点）。
4. `agent/run_batch.py` 仅会切换 `.env` 里的 `LAB_LLM_MODEL`，不会自动切换 reasoning/thinking 参数。

**关键结论**：

- 目前代码里，推理参数的唯一注入点就是 `agent/run.py` 的 `model_kwargs`。
- 目前 `.env.example` 没有现成字段可直接切换“思考 level”。
- 也就是说：你要切推理 level，当前需要在 `run.py` 里对 `model_kwargs` 增补参数（或自行扩展 env 解析逻辑）。

---

## 2. 在哪里加推理参数（统一入口）

在 `agent/run.py` 的如下位置加：

- 先创建：
  - `model_kwargs = {"temperature": 1.0, "timeout": 300}`
- 再补：
  - `api_base` / `api_key`
- 最后传给：
  - `get_model(input_model_name=..., config={"model_kwargs": model_kwargs, ...})`

因此你可以在这段中间增加类似：

```python
model_kwargs["reasoning"] = {"effort": "high"}
# 或
model_kwargs["extra_body"] = {"thinkingLevel": "high"}
```

建议原则：

- **OpenAI 原生参数**（如 `reasoning.effort`）优先用顶层字段。
- **供应商特有参数**（如 Gemini/Qwen/DeepSeek/Claude 的 thinking 字段）优先放在 `extra_body`，可减少网关适配差异。

---

## 3. 各模型推理 level 设置说明（按你给的验证结果）

> 下文“推荐设置”均指在 `model_kwargs` 中添加；如有 `extra_body`，可使用：
>
> ```python
> model_kwargs.setdefault("extra_body", {}).update({...})
> ```

### 3.1 OpenAI 系列

#### A) `gpt-5.3-codex` / `gpt-5.2-codex`

- 文档支持：`reasoning.effort = low | medium | high | xhigh`
- 验证：
  - `low/medium/high/xhigh`：通过
  - `none`：正确拒绝（Reasoning mandatory）
  - `minimal`：网关“意外通过”（文档未列出）

**推荐设置（稳定优先）**：

```python
model_kwargs["reasoning"] = {"effort": "low"}      # 或 medium/high/xhigh
```

**建议**：不要依赖 `minimal` 这种“文档未声明但偶尔通过”的行为。

---

#### B) `gpt-5.4`

- 文档支持：`none(默认) | low | medium | high | xhigh`
- 验证：
  - 上述 5 档都通过
  - `minimal` 严格拒绝（明确 unsupported）
- 特征：OpenAI 里对 effort 枚举校验最严格的一类（与 5.2 同规则）。

**推荐设置**：

```python
model_kwargs["reasoning"] = {"effort": "none"}     # 或 low/medium/high/xhigh
```

---

#### C) `gpt-5-mini`

- 文档支持：`minimal | low | medium(默认) | high`
- 验证：
  - 上述 4 档通过
  - `none` 拒绝（Reasoning mandatory）
  - `xhigh` 拒绝（unsupported）

**推荐设置**：

```python
model_kwargs["reasoning"] = {"effort": "minimal"}  # 或 low/medium/high
```

---

### 3.2 Google Gemini 系列

#### A) `gemini-3.1-pro-preview` / `customtools`

- 文档支持：`thinkingLevel = low | medium | high`
- 验证：
  - `low/medium/high` 通过
  - `minimal` 意外通过（文档说不支持）
  - 旧参数 `thinking_budget=1024` 通过

**推荐设置**（兼容网关透传）：

```python
model_kwargs.setdefault("extra_body", {}).update({
    "thinkingLevel": "high"   # low/medium/high
})
```

`minimal` 不建议作为正式配置依赖。

---

#### B) `gemini-3.1-flash-lite-preview`

- 文档支持：`thinkingLevel = minimal(默认) | low | medium | high`
- 验证：全部通过，与文档一致。

**推荐设置**：

```python
model_kwargs.setdefault("extra_body", {}).update({
    "thinkingLevel": "minimal"   # 或 low/medium/high
})
```

---

#### C) `gemini-3-pro-preview`（已废弃，2026-03-09 关闭）

- 历史验证：`low/medium/high` + `thinking_budget` 可通过。
- 由于模型已下线，建议迁移到 3.1 系列。

---

### 3.3 DeepSeek 系列

#### A) `deepseek-reasoner`

- 文档：模型本身即推理模式，无需独立开关。
- 验证：baseline 即有推理信号；额外传 `thinking.type=enabled` 也可通过。

**推荐设置**：

- 默认不额外设置即可。
- 若网关要求显式参数，可尝试：

```python
model_kwargs.setdefault("extra_body", {}).update({
    "thinking": {"type": "enabled"}
})
```

---

#### B) `deepseek-chat`

- 文档支持：`thinking.type = enabled`（开启）
- 验证：
  - `enabled`：有推理信号
  - `disabled`：无推理信号（文档未显式写 disabled，但网关接受）

**推荐设置**：

```python
model_kwargs.setdefault("extra_body", {}).update({
    "thinking": {"type": "enabled"}   # 或 disabled
})
```

---

### 3.4 Anthropic Claude 系列

#### A) `claude-sonnet-4.6`

- 文档支持：
  - 推荐：`thinking.type = adaptive`
  - `output_config.effort = low | medium | high`（文档写 Sonnet 不支持 `max`）
  - deprecated：`thinking.type = enabled` + `budget_tokens >= 1024`
- 验证：
  - `adaptive + effort(low/medium/high)` 通过
  - `effort=max` 意外通过（不建议依赖）
  - `enabled + budget_tokens` 通过
  - 裸 `enabled`（无 budget_tokens）会报错

**推荐设置**：

```python
model_kwargs.setdefault("extra_body", {}).update({
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "high"}   # low/medium/high
})
```

---

#### B) `claude-opus-4.6`

- 文档支持：
  - 推荐：`thinking.type = adaptive`
  - `output_config.effort = low | medium | high | max`（仅 Opus 支持 `max`）
  - deprecated：`enabled + budget_tokens >= 1024`
- 验证：与 Sonnet 类似，且 `max` 正常通过。

**推荐设置**：

```python
model_kwargs.setdefault("extra_body", {}).update({
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "max"}   # 仅 Opus 建议使用 max
})
```

若走 deprecated 路径，必须带 `budget_tokens`，否则会报错。

---

### 3.5 Qwen 系列

#### `qwen3.5-plus`

- 文档支持：
  - `enable_thinking = true|false`（默认 true）
  - `thinking_budget = 整数`（最大 81920）
- 验证：
  - `enable_thinking=true` 有推理信号
  - `enable_thinking=false` 无推理信号
  - `thinking_budget=1024/81920` 通过
  - `extra_body` 传参有效

**推荐设置**：

```python
model_kwargs.setdefault("extra_body", {}).update({
    "enable_thinking": True,
    "thinking_budget": 4096   # 可按需调到 1024 ~ 81920
})
```

---

## 4. 推荐的 level 选择策略（实战）

可按“修复难度/成本预算”选档：

1. **快速冒烟**：
   - OpenAI: `low` 或 `none`（仅 gpt-5.4 可用 none）
   - Gemini/Qwen: `minimal` 或低预算
2. **常规修复（默认）**：
   - `medium`
3. **复杂逻辑/长链推理**：
   - `high`
4. **最难问题冲刺**：
   - OpenAI Codex: `xhigh`
   - Claude Opus: `max`

注意：

- 不同模型可用档位不一致，不要跨模型生搬硬套。
- 对“文档未声明但网关接受”的值（如某些 `minimal` / `max`），仅可做实验，不建议作为生产默认值。

---

## 5. 与 `agent` 运行方式的配合建议

### 5.1 单模型运行

- 现在可以直接通过 `run.py` 参数指定模型和思考参数，无需改 `.env` 的模型字段。
- 常见示例：

```bash
# 例1：OpenAI GPT-5.4，用通用强度（会自动映射到 reasoning.effort）
uv run python agent/run.py 104772 \
  --model openai/gpt-5.4 \
  --effort high

# 例2：Qwen，显式控制 thinking 开关和预算
uv run python agent/run.py 104772 \
  --model qwen3.5-plus \
  --enable-thinking true \
  --thinking-budget 4096

# 例3：Claude Opus，显式传 adaptive + output effort
uv run python agent/run.py 104772 \
  --model claude-opus-4.6 \
  --thinking-type adaptive \
  --output-effort max
```

### 5.2 多模型批跑（`run_batch.py`）

`run_batch.py` 现在支持按顺序批量传模型与思考参数：

- `--model/--models`：按顺序指定模型。
- `--effort/--efforts`：按顺序指定通用思考强度（1 个值可应用到全部模型；N 个值与 N 个模型一一对应）。
- `--thinking-profile` / `--thinking-profiles-json`：按顺序传每个模型的详细 JSON 配置（可覆盖 `effort`）。

示例：

```bash
# 例1：两个模型按序对应两个强度
uv run python agent/run_batch.py \
  --model openai/gpt-5.4 \
  --model qwen3.5-plus \
  --issues 104772 \
  --effort high \
  --effort medium

# 例2：两个模型按序指定详细 profile（JSON）
uv run python agent/run_batch.py \
  --model openai/gpt-5.3-codex \
  --model claude-opus-4.6 \
  --issues 104772 \
  --thinking-profile '{"reasoning_effort":"xhigh"}' \
  --thinking-profile '{"thinking_type":"adaptive","output_effort":"max"}'
```

说明：

- `run_batch.py` 通过 `run.py --model` 传参，不再改写 `agent/.env`。
- profile 中未识别字段会默认透传到 `extra_body`，用于覆盖网关私有参数。

---

## 6. 常见报错与定位

1. `Reasoning is mandatory for this endpoint`
   - 说明当前模型不允许 `none` / 未传 reasoning。
2. `Unsupported value: ...`
   - 说明档位枚举非法（如某模型不支持 `minimal` 或 `xhigh`）。
3. Claude `BudgetTokens is nil`
   - 使用 `thinking.type=enabled` 时缺少 `budget_tokens`。

建议先对照本指南中的“模型-档位支持”检查，再看网关是否做了额外限制。

---

## 7. 最小落地模板（可直接参考）

在 `agent/run.py` 的 `model_kwargs` 初始化后增加一段“按模型注入”的逻辑：

```python
m = cfg.llm_model.lower()
extra = model_kwargs.setdefault("extra_body", {})

if "gpt-5.3-codex" in m or "gpt-5.2-codex" in m:
    model_kwargs["reasoning"] = {"effort": "high"}
elif "gpt-5.4" in m:
    model_kwargs["reasoning"] = {"effort": "medium"}
elif "gpt-5-mini" in m:
    model_kwargs["reasoning"] = {"effort": "minimal"}
elif "gemini-3.1-pro-preview" in m:
    extra.update({"thinkingLevel": "high"})
elif "gemini-3.1-flash-lite-preview" in m:
    extra.update({"thinkingLevel": "minimal"})
elif "deepseek-chat" in m:
    extra.update({"thinking": {"type": "enabled"}})
elif "claude-sonnet-4.6" in m:
    extra.update({"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}})
elif "claude-opus-4.6" in m:
    extra.update({"thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}})
elif "qwen3.5-plus" in m:
    extra.update({"enable_thinking": True, "thinking_budget": 4096})
```

这段不是仓库现有逻辑，而是可直接采用的最小改造模板。

---

## 8. 结论

- 当前 `agent` 的模型调用已经具备统一入口（`model_kwargs`），非常适合做推理参数分流。
- 你给出的验证结果里，部分网关对未文档化值会“意外放行”；实践中应以**官方文档支持值**为主。
- 若后续想把“推理 level”做成可配置项，建议在 `env_config.py` 新增统一字段，然后在 `run.py` 一处注入，避免每次手改代码。
