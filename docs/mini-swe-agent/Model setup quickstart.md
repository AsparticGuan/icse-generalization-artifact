# Model setup quickstart

---

# 1) 第一次使用：一条命令把“默认模型 + API Key”配好

## 你要做什么

第一次运行（或你想重新配置）时，直接执行：

```bash
mini-extra config setup
```

官方建议：这应该在你第一次跑 `mini` 前/后做一次，而且很多情况下 `mini` 第一次运行也会自动触发这个设置流程。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

## 它会发生什么（实现原理的“人话版”）

`mini-extra config setup` 本质是一个**交互式写配置**的小工具：

- 它会问你：默认模型名（例如 `anthropic/claude-...`）
- 再问你：API key 的“变量名”（例如 `ANTHROPIC_API_KEY`）和“变量值”（例如 `sk-...`）
- 然后把这些键值对写进 mini 的**全局配置文件（一个 `.env` 文件）**

从实现上看，它用的是 `dotenv.set_key(...)` 往全局 `.env` 文件里写入键值，并且用 `MSWEA_CONFIGURED` 这个开关判断“是不是第一次配置”。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/run/config/?utm_source=chatgpt.com))

---

# 2) API Key 怎么设：三种方式 + 优先级（非常关键）

文档列了三种方式（推荐顺序也是这个）： ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

## A. 推荐：用 setup 脚本一次性设置（最省心）

```bash
mini-extra config setup
```

适合：第一次配好后长期使用。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

## B. 写进 mini 的全局配置文件（`.env`）：`mini-extra config set`

例如把 Anthropic key 写进去：

```bash
mini-extra config set ANTHROPIC_API_KEY sk-xxxx
```

这会把 key 写进全局 config 文件（`.env`）。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

你还可以：

```bash
mini-extra config unset ANTHROPIC_API_KEY
mini-extra config edit
```

其中 `edit` 会打开编辑器（默认 `EDITOR` 环境变量，没设就用 `nano`）直接编辑那个 `.env`。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/run/config/?utm_source=chatgpt.com))

## C. 临时：直接 export 环境变量（关掉 shell 就没了）

```bash
export ANTHROPIC_API_KEY=sk-xxxx
```

适合：临时测试、CI、或你不想把 key 写到文件里。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

## 优先级（一定要记住）

**环境变量 > `.env` 文件。**

也就是：你 export 了一个同名变量，会覆盖 `.env` 里的值。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/global_configuration/))

---

# 3) 选模型：一次性指定 vs 设默认值 vs 写到 agent config

## 一次性指定（只影响这次运行）

所有 CLI 都可以用 `-m/--model` 指定模型名。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

例子（用 Claude 跑一次）：

```bash
mini -m "anthropic/claude-sonnet-4-5-20250929"
```

例子（用 GPT-5 跑一次）：

```bash
mini -m "openai/gpt-5"
```

> 重要：模型名一般要带 provider 前缀，比如 `anthropic/...`、`openai/...`（文档在 setup 的提示里也强调了这一点）。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/run/config/?utm_source=chatgpt.com))
> 

## 设置默认模型（影响以后所有运行）

你可以把默认模型写入全局 config 的 `MSWEA_MODEL_NAME`：

```bash
mini-extra config set MSWEA_MODEL_NAME "anthropic/claude-sonnet-4-5-20250929"
```

或用环境变量：

```bash
export MSWEA_MODEL_NAME="anthropic/claude-sonnet-4-5-20250929"
```

文档明确支持这几种方式。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

### 运行时“到底用哪个模型名”（实现逻辑）

`get_model_name(...)` 的优先级是：

1. 你传入的 `m/--model`
2. agent config 里的 `model_name`
3. 环境变量 `MSWEA_MODEL_NAME`
    
    都没有就直接报错，提示你去跑 `mini-extra config setup`。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/models/utils/))
    

---

# 4) 为什么 Anthropic 常被“特殊对待”（cache control 的由来）

mini 会对 Anthropic/Claude/Sonnet/Opus 这类模型默认加 cache control 设置（如果你没手动配置过），以便更好地处理 Anthropic 相关的缓存断点行为。

实现上就是：模型名里匹配到 `anthropic/claude/sonnet/opus` 等关键词时，自动写入 `set_cache_control="default_end"`。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/models/utils/))

---

# 5) Extra model settings：温度、reasoning effort、Responses API（都在 agent config 里改）

文档说得很直接：要配 reasoning effort 之类的参数，需要编辑 **agent config file**，并且新版会在你运行 `mini` 时把 config 文件位置打印出来。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

## 例子 1：给 Claude 设 temperature

```yaml
model:
  model_name: "anthropic/claude-sonnet-4-5-20250929"
  model_kwargs:
    temperature: 0.0
```

([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

## 例子 2：GPT-5（Chat Completions 参数风格）

```yaml
model:
  model_name: "openai/gpt-5-mini"
  model_kwargs:
    drop_params: true
    reasoning_effort: "high"
    verbosity: "medium"
```

`drop_params: true` 用来丢掉不被模型支持的参数，避免接口报错。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

## 例子 3：GPT-5 用 Responses API（原生 tool calling + 跨轮对话状态）

文档示例（注意 model_class）： ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

```yaml
model:
  model_class: "litellm_response_toolcall"
  model_name: "openai/gpt-5-mini"
  model_kwargs:
    drop_params: true
    reasoning:
      effort: "high"
```

这个 `litellm_response` 家族的模型类会用 OpenAI Responses API，支持原生工具调用，并且用 `previous_response_id` 维持多轮对话状态。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/models/litellm_response_toolcall/))

---

# 6) 用 OpenRouter / Portkey：两种写法（CLI 或 agent config）

文档总结：默认通常走 `litellm`，但你可以用 `--model-class` 或在 agent config 里写 `model_class` 来切后端。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

## OpenRouter（例子）

**CLI：**

```bash
mini -m "moonshotai/kimi-k2-0905" --model-class openrouter
```

**或 agent config：**

```yaml
model:
  model_name: "moonshotai/kimi-k2-0905"
  model_class: openrouter
```

([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

OpenRouterModel 会从环境变量读取 `OPENROUTER_API_KEY`，并且在 401 时会提示你用 `mini-extra config set OPENROUTER_API_KEY ...` 来永久设置。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/models/openrouter/))

## Portkey（例子）

**CLI：**

```bash
mini -m "claude-sonnet-4-5-20250929" --model-class portkey
```

**或 agent config：**

```yaml
model:
  model_name: "claude-sonnet-4-5-20250929"
  model_class: portkey
```

([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))

PortkeyModel 需要 `PORTKEY_API_KEY`（从环境变量读取），没设置会直接报错，并明确告诉你可以用 `mini-extra config set PORTKEY_API_KEY YOUR_KEY` 永久写入。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/models/portkey/))

另外它目前仍用 `litellm` 做 cost 计算，模型名不一致时可以用 `litellm_model_name_override` 兜底。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/models/portkey/))

---

# 7) 最快自检清单（遇到报错时从这 3 条查起）

1. **默认模型没设**：如果你没传 `m`，也没在 agent config 里写 `model_name`，也没设 `MSWEA_MODEL_NAME`，就会提示你跑 `mini-extra config setup`。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/reference/models/utils/))
2. **API key 名字不对**：确认你用的是该 provider 需要的环境变量名（文档列了 litellm 支持的 key 名单，如 `OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY / OPENROUTER_API_KEY ...`）。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/models/quickstart/))
3. **你以为写进 `.env` 生效但其实被环境变量覆盖了**：环境变量优先级更高。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/global_configuration/))