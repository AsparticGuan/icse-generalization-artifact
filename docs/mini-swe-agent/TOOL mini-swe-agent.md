# mini-swe-agent v2：关于 Tool（工具调用）设计与变化（含实现解读 + 例子）

> 关键词：**从“文本正则解析命令”切到“原生 tool calling”**（v2 默认），并且 **trajectory（轨迹文件）更贴近模型原生返回结构**。
> 

---

## 1. v2 里 “tool” 到底指什么？

在 mini-swe-agent 的设计里，**“tool”几乎等同于一个：`bash`**（让模型通过 shell 完成所有事），而不是像一些框架那样有一堆 file viewer / editor / search 工具。官方仓库也明确强调了 *“只用 bash”* 的极简取向。 ([GitHub](https://github.com/SWE-agent/mini-swe-agent?utm_source=chatgpt.com))

在 v2 的 tool-calling 路径里，这个 `bash` 工具会被声明成一个标准的 *function tool*（OpenAI 风格 schema），其参数只有一个字段：`command`： ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/models/utils/actions_toolcall.py))

```json
{
  "type": "function",
  "function": {
    "name": "bash",
    "description": "Execute a bash command",
    "parameters": {
      "type": "object",
      "properties": {
        "command": { "type": "string" }
      },
      "required": ["command"]
    }
  }
}
```

---

## 2. v1 → v2：核心变化 = 从“正则抠命令”到“原生 tool calling”

v2 migration guide 把差异说得很直接： ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/v2_migration/))

- **Tool calling（v2 默认）**：模型用原生 tool calling API 调用 `"bash"` 工具
- **Text-based（legacy）**：模型在文本里输出代码块/标记，框架用 regex 抽取命令

### 2.1 Tool calling（默认推荐）长什么样？

**你不再要求模型把命令写进 markdown code block 给你正则抽取**，而是让模型直接返回结构化的 tool call，例如（示意）：

```json
{
  "role": "assistant",
  "content": "我先看下目录结构。",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "bash",
        "arguments": "{\"command\":\"ls -la\"}"
      }
    }
  ]
}
```

然后 mini-swe-agent 会把 tool_calls 解析成内部统一的 `actions`（只保留 command + tool_call_id），并执行。

**实现上（LitellmModel）：**

1. 调用 LiteLLM 时把 `tools=[BASH_TOOL]` 传给模型（这一步就是“启用原生 tool calling”）： ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/models/litellm/))
2. 从 `response.choices[0].message.tool_calls` 里取 tool calls，解析成 actions： ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/models/litellm/))
3. 把解析结果塞进 `message["extra"]["actions"]`： ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/models/litellm/))

---

### 2.2 Text-based（legacy / 兼容模式）长什么样？

仍然是你熟悉的这种输出：

```markdown
```mswea_bash_command
ls -la
```

```

然后用 `action_regex` 把代码块里的命令抓出来。

v2 仍然支持这种模式，但它被明确标成 legacy，并且你需要切到对应的 model_class，例如 migration guide 给的例子： :contentReference[oaicite:6]{index=6}

```yaml
model:
  model_class: litellm_textbased
  action_regex: "```mswea_bash_command\\s*\\n(.*?)\\n```"
```

---

## 3. v2 的“工具调用执行链”是怎么跑起来的（结合实现）

你可以把一次 step 理解成下面这条流水线（**模型决定要跑什么命令** → **环境执行** → **把执行结果喂回模型**）：

### 3.1 Agent 主循环：只认 `message.extra.actions`

`DefaultAgent` 的核心逻辑非常直接：

- `query()`：问模型要下一步
- `execute_actions()`：把 `message.extra.actions` 里的每个 action 丢给 env 执行，然后把 observation messages 追加回 messages 历史里 ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/agents/default.py))

这也解释了一个关键设计点：

> **不管你是 tool calling 还是 text-based，最终都要落到同一种内部表示：`extra.actions = [{command: ...}, ...]`。**
> 

---

### 3.2 Tool calling 的 action 解析：强约束 + 可自动纠错

`parse_toolcall_actions()` 做了几件很“硬”的事： ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/models/utils/actions_toolcall.py))

- **如果模型没给任何 tool call：直接抛 `FormatError`**，并提示 “Every response MUST include at least one tool call”
- **如果 tool 不是 `bash`：报 Unknown tool**
- **如果 arguments 不是合法 JSON 或缺少 `command`：报错**

`FormatError` 属于一种“中断 agent 流程并追加提示消息”的异常类型（`InterruptAgentFlow` 家族），用于把“格式错误反馈”塞回对话让模型重试： ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/exceptions.py))

---

### 3.3 环境执行：每个 action 都是独立 subshell（目录不持久）

以 `LocalEnvironment` 为例，它每次 action 都是 `subprocess.run(..., shell=True, cwd=...)` 单次执行： ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/environments/local.py))

这带来你在 prompt 里经常强调的规则：

- **`cd` 不会在下一步保留**
- 所以要写成：`cd /repo && <command>`（或者用 `cwd` 配置固定工作目录）

---

### 3.4 observation（工具执行结果）怎么回传给模型？

tool calling 模式下，执行结果会被包装成 **role=tool** 的消息，并且会带上 `tool_call_id` 来对应模型当初那条 tool call： ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/models/utils/actions_toolcall.py))

> 这点非常关键：它让“工具请求 ↔ 工具结果”在结构上严格配对，比“把输出贴回 user 文本”更稳定。
> 

---

### 3.5 任务结束的“提交信号”也是一种工具链设计

`LocalEnvironment` 会检查输出的第一行是不是 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`，如果是就抛 `Submitted` 让 agent 退出： ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/environments/local.py))

---

## 4. 配置层面：v2 你到底该怎么用？

### 4.1 默认（tool calling）——基本不用改

migration guide 明确说：CLI 的默认 `mini.yaml / swebench.yaml` 已经是 tool calling 配置。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/v2_migration/))

你只要：

```bash
mini
# 或
mini-extra swebench
```

### 4.2 自定义 config：你需要选对 model_class

- **要 tool calling（默认推荐）**：`model_class: litellm` ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/v2_migration/))
- **要 text-based（legacy）**：`model_class: litellm_textbased + action_regex` ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/v2_migration/))

---

## 5. v2 关于 “自定义 action 解析” 的设计点（很多人会踩坑）

yaml guide 里强调了两条非常实用的约束：

### 5.1 你一旦自定义 `action_regex`，所有 prompt 模板必须一致

例如你决定用 `<mswea_bash_command>...</mswea_bash_command>`，那 system_template / instance_template / format_error_template 等都要统一要求模型用同一种包裹格式，否则模型“按 A 模板输出”，你却“按 B 正则抽取”，必炸。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

### 5.2 `action_regex` 的 yaml 转义/换行问题

文档建议：**regex 放单行、不要加引号、不要用 `|`**，否则你可能不小心引入末尾换行导致匹配异常。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

## 6. v2 的 trajectory（轨迹文件）与 tool 的关系：为什么它也算“tool 设计变化”？

v2 轨迹格式的变化是：**messages 不再被“手工规整成 role/content 字符串”，而是以“模型原生返回结构”为基准，只额外加 `extra` 字段**。 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/v2_migration/))

这对 tool calling 很重要，因为：

- tool calling 的关键信息（如 `tool_calls`）本来就是模型原生结构的一部分
- v2 选择“保留原生结构”，意味着你做离线分析/回放时信息更全
- 代价是：不同 provider / endpoint（比如 OpenAI `/responses`）消息结构可能不同，写解析器要更通用 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/v2_migration/))

---

## 7. 三个“简单但真实”的例子（你可以直接对照理解）

### 例子 A：tool calling 一次完整回合（从模型到执行再到回传）

1. 模型返回 tool call（bash: `pytest -q`）
2. parser 把它变成 `extra.actions=[{command:"pytest -q", tool_call_id:"..."}]` ([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/models/litellm/))
3. env 执行 `pytest -q`（独立 subshell） ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/environments/local.py))
4. 把输出包装成 role=tool 的 observation，并带 tool_call_id 对应回去 ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/models/utils/actions_toolcall.py))

---

### 例子 B：为什么 tool calling 比 regex 稳（一个常见失败场景）

text-based 里常见模型输出：

- 少了 code fence
- fence 名字写错（`mswea_bash` vs `mswea_bash_command`）
- 多输出了两个 code block（你只允许一个）

结果就是 regex 抽不到命令，或抽到错误命令。

tool calling 下，`parse_toolcall_actions()` 会明确检查：**没 tool call 就 FormatError；tool 名不对/参数不对也 FormatError**，并把错误通过 `format_error_template` 反馈给模型重试。 ([GitHub](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/models/utils/actions_toolcall.py))

---

### 例子 C：你想用 XML 包裹命令（legacy 解析），该怎么配？

核心就是两点：

1. `model.action_regex` 改成 `<mswea_bash_command>(.*?)</mswea_bash_command>`
2. 所有模板统一要求模型按这个格式输出 ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

（并注意 regex 写法按文档建议保持单行避免 yaml 换行坑） ([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

# 简单例子

---

## 例子 1：默认（tool calling）你几乎不用写 `action_regex`

**目标**：用 v2 默认方式运行，让模型通过原生 tool calling 调 `bash`。

**mini.yaml（最小关键信息示意）**

```yaml
model:
  model_class: litellm   # ✅ tool calling 默认
  model_name: anthropic/claude-sonnet-4-5-20250929
```

**你在命令行里：**

```bash
mini
```

**你得到的效果**：模型不会被要求输出 `mswea_bash_command` 这种文本代码块；它会直接返回结构化 tool_calls，然后框架执行。

> 设计点：你把“命令是否能被解析”从 regex 的脆弱匹配，升级成 API 层面结构化字段。
> 

---

## 例子 2：同一个“ls”，tool calling vs 文本解析长什么样？

### 2.1 tool calling（v2 默认）

模型返回（示意）：

```json
{
  "role": "assistant",
  "content": "我先看下目录结构。",
  "tool_calls": [
    {
      "id": "call_123",
      "type": "function",
      "function": {
        "name": "bash",
        "arguments": "{\"command\":\"ls -la\"}"
      }
    }
  ]
}
```

框架内部统一成：

```json
{
  "extra": {
    "actions": [
      {"tool": "bash", "command": "ls -la", "tool_call_id": "call_123"}
    ]
  }
}
```

### 2.2 text-based（legacy）

模型输出（示意）：

```markdown
```mswea_bash_command
ls -la
```
```

框架用 regex 抓到 `ls -la`，再同样变成 `extra.actions=[...]`。

> 设计点：**无论输入来自 tool_calls 还是 regex，最终都落到同一个内部结构 `extra.actions`**。Agent 后续执行逻辑因此“完全不用关心你用哪种模式”。
> 

---

## 例子 3：为什么 v2 默认 tool calling 更稳——“格式错了就能纠错”

### 3.1 legacy 常见失败：模型写错 fence 名字

你期望 `mswea_bash_command`，模型却写成：

```markdown
```bash
ls -la
```
```

结果：regex 抓不到命令 → agent 卡住/报格式错误。

### 3.2 tool calling 的失败更“可控”

如果模型忘了给 tool call（只说话不调用 bash）：

```json
{"role":"assistant","content":"我先想想。"}
```

框架会抛 `FormatError`，并把**明确的纠错提示**（来自 `format_error_template`）塞回对话，让模型重试，下一轮就更容易回到正确格式。

> 设计点：把“格式约束”变成**可程序化验证的协议**，而不是在文本里靠模型自觉。
> 

---

## 例子 4：subshell 不持久 → 为什么 prompt 里强调 `cd /path && ...`

**目标**：解释 “每个 action 是独立 subshell，cd 不会保留”。

如果你两步走：

```bash
cd /repo
```

下一步：

```bash
pytest -q
```

第二步其实很可能**不在 /repo**，因为每一步是新 subshell。

正确写法（同一步里把目录固定住）：

```bash
cd /repo && pytest -q
```

或者（如果你在环境里配了 `cwd: "/repo"`）就可以直接：

```bash
pytest -q
```

> 设计点：执行环境的“无状态”让 agent 更可复现；代价是需要在每次 action 里显式指定工作目录。
> 

---

## 例子 5：tool result 为什么要带 `tool_call_id`（对齐请求与结果）

**目标**：展示 tool calling 的“请求—结果”严格配对。

模型发起：

```json
"tool_calls": [
  {"id":"call_abc", "function":{"name":"bash","arguments":"{\"command\":\"ls\"}"}}
]
```

系统执行完返回（示意）：

```json
{
  "role": "tool",
  "name": "bash",
  "tool_call_id": "call_abc",
  "content": "{\"returncode\":0,\"output\":\"a.py\\nb.py\\n\"}"
}
```

> 设计点：`tool_call_id` 让模型能明确知道“这段输出对应我哪一次调用”，避免多次工具调用时输出串线（这是 regex 文本拼接很难稳定保证的）。
> 

---

## 例子 6：自定义 legacy 解析格式（XML 代替 Markdown fence）

**目标**：你想让模型输出 XML 标签包命令，而不是 `code block`。

配置：

```yaml
model:
  model_class: litellm_textbased
  action_regex: <mswea_bash_command>(.*?)</mswea_bash_command>
```

同时你的 prompt 模板里必须统一要求模型按这个输出：

```
<mswea_bash_command>ls -la</mswea_bash_command>
```

> 设计点：**regex 解析完全依赖“输出格式一致性”**。一旦你换了 action_regex，就要把 system/instance/format_error 等模板全部同步。
> 

---

## 例子 7：输出太长怎么办？`observation_template` 的“截断”策略

**目标**：避免 `find /` 这种命令输出爆炸导致模型上下文被淹没。

v2 常见做法：在 `observation_template` 里做“< 10000 字符完整输出，否则只给 head/tail + elided_chars”。

你会看到类似结构（示意）：

```json
{
  "returncode": 0,
  "output_head": "...前 5000 字符...",
  "output_tail": "...后 5000 字符...",
  "elided_chars": 238745,
  "warning": "Output too long."
}
```

> 设计点：工具输出是“观测”，观测需要被**结构化和压缩**，否则 agent 会被无关噪声拖死。
> 

---

## 例子 8：为什么用 `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 作为“终止信号”？

**目标**：解释 v2 里“提交即退出”的硬边界。

当模型执行：

```bash
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
```

环境侧会检测到这个标记，然后抛出 `Submitted` 异常（控制流跳出），agent 结束运行。

> 设计点：用“可检测的 shell 输出信号”统一收口 agent 退出逻辑，比让模型口头说“我完成了”更可靠、更可自动评测。
> 

---