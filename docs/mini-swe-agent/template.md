# template

# mini-SWE-agent V2 的“模板”是什么？

在 mini-SWE-agent V2 里，“模板（template）”不是“代码生成模板/脚手架”那种含义，而是**用来动态生成提示词（prompt）与观测（observation）的 Jinja2 文本模板**：

系统把这些模板渲染成字符串，作为每轮发给模型的 messages（system/user/observation/错误纠正提示）的一部分。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

# 1) 模板分两大类：Agent 模板 vs Model 模板

## A. Agent 侧：Prompt 模板（决定“开局怎么要求模型做事”）

在 YAML 配置的 `agent:` 里，你最常见会看到：

- `system_template`：**第一条 system message**（“你是谁/你必须遵守什么格式/你能用什么能力”）([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/agents/default/))
- `instance_template`：**第一条 user message**（把具体任务 `{{task}}` 填进去，同时给工作流/规则/示例）([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/agents/default/))

这些模板是“开局设定 + 行为约束”的核心：你写什么，模型就更倾向怎么行动。

## B. Model 侧：格式化模板（决定“每步执行后，把结果怎么喂回模型”）

在 YAML 配置的 `model:` 里，你常见会看到：

- `observation_template`：把一次 bash 执行结果（return code、stdout/stderr、异常信息等）**渲染成模型可读的观测文本**([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))
- `format_error_template`：当模型输出**不符合预期格式**（比如没给 bash 工具调用、或动作解析失败）时，用这个模板生成一条“纠错提示”再喂回模型，促使它下一次按规则输出([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

> 直观类比：
> 
> 
> **Agent 模板**=“教模型怎么干活（规则/流程/输出格式）”
> 
> **Model 模板**=“把环境执行结果翻译成模型更容易用的信息（以及出错时怎么提醒它改正）”
> 

---

## 例子任务

**任务**：修复测试 `test_parse_date` 失败。

---

### A) Agent 模板（Prompt 模板）= “开局教模型怎么干活”

### 1) `system_template`（第一条 system）

**作用**：定规矩（能用啥、必须怎么输出）

**简洁例子：**

```yaml
agent:
  system_template: |
    你是代码修复助手。你只能通过 bash 命令操作仓库。
    每次需要执行命令时，必须输出一个 mswea_bash_command 代码块。

```

**直观效果**：模型会更像“按格式给命令的人”，而不是只给建议。

---

### 2) `instance_template`（第一条 user）

**作用**：把具体任务塞进去 + 给工作流（先复现→定位→修改→验证）

**简洁例子：**

```yaml
agent:
  instance_template: |
    任务：{{ task }}
    请按流程：1) 运行相关测试复现 2) 定位原因 3) 修改代码 4) 重新跑测试确认

```

运行时 `task="修复 test_parse_date 失败"`，渲染后模型看到的就是：

- “任务：修复 test_parse_date 失败”
- “按流程做 1-4”

---

### B) Model 模板（格式化模板）= “把执行结果翻译给模型 / 出错时纠正它”

### 3) `observation_template`（每次命令执行后）

**作用**：把“命令执行结果”整理成模型更好读、好判断的文本（尤其 returncode）

**简洁例子：**

```yaml
model:
  observation_template: |
    [OBS]
    rc={{ output.returncode }}
    out={{ output.output }}

```

如果模型刚跑了 `pytest -q -k test_parse_date`，真实输出是失败，那么模型下一轮会收到类似：

- `rc=1`
- `out=AssertionError ...`

**直观效果**：模型很容易根据 `rc=1` 知道“上一步失败，继续定位/修”。

---

### 4) `format_error_template`（模型输出格式错了）

**作用**：当模型没按你要求的格式输出命令，系统用这条“纠错提示”把它拉回正轨

**简洁例子：**

```yaml
model:
  format_error_template: |
    你的输出无法解析为命令：{{ error }}
    请用 mswea_bash_command 代码块输出 bash 命令。

```

场景：模型说了句“你可以跑 pytest -q”，但**没给代码块** → 系统解析失败 → 回它这条纠错提示。

**直观效果**：下一轮模型更可能改成“按代码块给命令”。

---

### 一句话对照（最重要）

- **Agent 模板（system/instance）**：决定“开局怎么要求模型做事”（规则 + 流程 + 任务注入）
- **Model 模板（observation/format_error）**：决定“每步执行后怎么把结果喂回去 + 出错怎么提醒它改正”

---

# 2) 模板是怎么被“渲染并用起来”的？

## 2.1 渲染引擎：Jinja2 + StrictUndefined

mini-SWE-agent 用 **Jinja2** 渲染模板；

并且启用 `StrictUndefined`——也就是你写了 `{{some_var}}` 但运行时没提供这个变量，会直接报错（避免“悄悄渲染成空字符串”这种隐蔽 bug）。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/agents/default/))

## 2.2 渲染时有哪些变量可用？

`DefaultAgent.get_template_vars()` 会把多路信息合并到一个 dict 里给模板用，大概包括：

- agent 配置本身（如 step_limit/cost_limit 等）
- environment 提供的变量
- model 提供的变量
- 运行中统计（如 `n_model_calls`、`model_cost`）
- `run(task=..., **kwargs)` 传入的变量（默认最重要的是 `task`）
    
    ([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/agents/default/))
    

YAML 文档也明确列了“内置 agent 可用变量”的范围，并特别点出：

**上一次 action 执行结果会以 `output` 变量进入模板**（这对 observation_template 很关键）。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

## 2.3 用起来的顺序（非常关键）

`DefaultAgent.run()` 启动时会：

1. 把 `task` 塞进模板变量
2. 渲染 `system_template` → 作为 system message
3. 渲染 `instance_template` → 作为第一条 user message
4. 进入循环：模型输出动作 → 环境执行 → 用 `observation_template` 格式化执行结果 → 继续下一轮
    
    ([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/agents/default/))
    

---

# 3) V2 里“模板字段”分别在控制什么？

下面按“你改它会改变什么行为”来解释：

## 3.1 `system_template`：全局规则与输出协议（最硬的约束）

它通常写这些内容：

- 允许能力：例如“你可以执行 bash 命令/编辑文件”等
- 输出结构：例如“每次回复必须包含 reasoning + 至少一次 bash tool call”等
- 安全/边界：例如“最终提交必须 echo 某个字符串，且不能与其他命令混在一起”
    
    ([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))
    

## 3.2 `instance_template`：任务注入 + 工作流脚手架（决定模型“怎么推进任务”）

它最核心就是把 `{{task}}` 注入进去，并给一个建议流程：先读仓库、复现、修改、验证、再提交。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

它也很适合放“跟环境相关”的提示——比如文档示例里就根据 `system == "Darwin"` 在模板里分支，提醒 macOS 的 `sed -i ''` 差异。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

## 3.3 `observation_template`：决定“模型看到的执行结果长什么样”

这会显著影响模型的稳定性与 token 成本：

- 你可以把 output 包装成 JSON / XML / 自定义结构
- 你可以在模板里做“长输出截断”（只给 head/tail + warning）
    
    文档给了完整示例：当输出太长时只保留头尾并提示模型换更小输出的命令。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))
    

## 3.4 `format_error_template`：模型不按规矩输出时，如何“拉回正轨”

比如模型忘了输出 bash 工具调用、或者动作解析失败时，会把 `{{ error }}` 或定制提示渲染出来，作为下一轮的纠错信息。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/models/litellm/))

---

# 4) “模板”与“动作格式/解析”的关系：你必须搞清的一条红线

mini-SWE-agent 支持两类动作来源：

- **原生 tool calls（如 LiteLLM + bash tool）**
- **从文本里用正则解析动作**（比如从 markdown code block 或 XML tag 里抠命令）

文档明确警告：

**如果你自定义 `action_regex`（例如改成 `<action>...</action>`），你必须在所有 prompt 模板里统一要求模型用同一种格式输出**（system_template / instance_template / format_error_template 等都要一致），否则模型会“被你自己写的提示词搞混”。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

# 5) 3 个“很实用、很简单”的模板改动例子（帮助你建立直觉）

## 例子 1：强制每条命令都在工作目录执行（应对“子 shell 不持久”）

mini 的环境执行是“每步一个新 subshell”，所以 `cd` 不会在下一步保留。

你可以直接在模板里把规则写死：要求模型每次命令都写成 `cd /path && ...`。

文档示例里也强调了“目录/环境变量变更不持久”的事实。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

你可以在 `system_template` 加一条类似（示意）：

```
每次 bash 命令都必须以：cd {{cwd}} && 开头（或等价方式）。
```

其中 `{{cwd}}` 来自 environment 配置变量（它会进模板变量）。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

## 例子 2：让 observation 变成“结构化 JSON”，模型更不容易漏关键信息

文档默认就展示了用 `observation_template` 输出 JSON（包含 returncode/output/exception_info），并且对超长输出做 head/tail 截断。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

这类模板的收益是：

- 模型更容易稳定提取：“returncode != 0 就是失败”
- 日志/训练数据更干净（更像可学习的结构化轨迹）

---

## 例子 3：不用 tool-calling，改用 XML tag 输出命令（适配更多模型/部署）

如果你的模型/网关不支持 tool calling，你可以改成“文本协议 + 正则解析”。

文档给了完整例子：把命令包在 `<mswea_bash_command>...</mswea_bash_command>` 里，并强调必须在所有模板里统一这种格式。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

这就是你之前问的“**不使用工具调用接口也能跑**”在 V2 的标准落地方式：

- **模板负责教模型输出成你可解析的形状**
- `action_regex` 负责把命令从文本里抠出来

---

# 6) 一句话总结你该怎么理解“模板”

把它当成 mini-SWE-agent V2 的“可编程提示词层”：

- **你用模板定义协议**（模型必须怎么输出、系统怎么纠错）
- **你用模板定义信息布局**（执行结果怎么呈现、长输出怎么裁剪）
- **你用模板把环境/配置/运行时信息注入给模型**（task、cwd、系统信息、成本统计等）
    
    ([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/agents/default/))
    

# 参考实际例子

## mini-SWE-agent v2：用简单实际例子理解“模板（template）”

> 你可以把“模板”理解成：
> 
> 
> **一段可渲染的文本（通常是 Jinja2），把变量（task/cwd/命令输出等）填进去，生成最终发给模型的消息。**
> 

---

## 例子 1：`instance_template` —— 把任务 `task` 注入到第一条 user 消息

### 你写的模板（示意）

```
你要完成的任务是：{{ task }}
请先运行测试复现问题，然后再修改代码并重新运行测试验证。
```

### 运行时传入变量

- `task = "修复 test_parse_date 失败"`

### 渲染后的真实 user 消息（模型看到的）

```
你要完成的任务是：修复 test_parse_date 失败
请先运行测试复现问题，然后再修改代码并重新运行测试验证。

```

✅ 直觉：`instance_template` 就是“任务说明书”，每次 run 的 task 会被插进去。

---

## 例子 2：`system_template` —— 强制模型遵守动作格式（不按就跑不起来）

场景：你希望模型**每次执行命令都必须用固定格式输出**（文本协议方式），系统才能解析并执行。

### 你写的模板（示意）

> 注意：为了避免 Notion 的“代码块嵌套代码块”问题，这里用 占位符描述动作代码块格式。
> 

```
你是一个代码修复助手。
你只能通过 bash 命令进行操作。

当你需要执行命令时，必须严格输出如下结构（不要在结构外输出命令）：

[动作代码块开始，语言标签=mswea_bash_command]
<在这里写 bash 命令>
[动作代码块结束]

```

### 模型因此会更倾向输出（示例）

```
我先运行测试复现问题。

[动作代码块开始，语言标签=mswea_bash_command]
pytest -q
[动作代码块结束]

```

✅ 直觉：`system_template` 是“法律条文/输出协议”，决定模型 **必须怎么给 action**。

---

## 例子 3：`observation_template` —— 把执行结果“翻译”成模型更容易用的样子

命令执行完，环境会返回结构化信息（示意）：

- `output.returncode = 1`
- `output.output = "AssertionError: expected 2024-01-02, got 2024/01/02"`

你可以通过 `observation_template` 决定“模型看到的观测文本长什么样”。

---

### 版本 A：朴素模板（直接回填）

### 模板（示意）

```
[command result]
returncode: {{ output.returncode }}
output:
{{ output.output }}

```

### 渲染后（模型看到的）

```
[command result]
returncode: 1
output:
AssertionError: expected 2024-01-02, got 2024/01/02

```

---

### 版本 B：结构化模板（更利于模型稳定提取关键信息）

### 模板（示意）

```
OBSERVATION(JSON):
{
  "returncode": {{ output.returncode }},
  "output": {{ output.output | tojson }}
}

如果 returncode != 0，说明上一步失败。
请先定位失败原因，再继续下一步操作。

```

### 渲染后（模型看到的）

```
OBSERVATION(JSON):
{
  "returncode": 1,
  "output": "AssertionError: expected 2024-01-02, got 2024/01/02"
}

如果 returncode != 0，说明上一步失败。
请先定位失败原因，再继续下一步操作。

```

✅ 直觉：`observation_template` 会显著影响模型行为（稳定性、漏信息概率、token 成本）。

---

## 例子 4：`observation_template` 截断长输出 —— 省 token，也更稳

场景：命令输出太长（比如测试日志上千行），你不想全喂给模型。

### 模板（示意：只保留头尾）

```
returncode: {{ output.returncode }}

{% set text = output.output %}
{% if text|length > 800 %}
输出太长，已截断：

--- HEAD (前400字符) ---
{{ text[:400] }}

--- TAIL (后400字符) ---
{{ text[-400:] }}

提示：请用更精确的命令获取关键信息（例如 pytest -k、rg、sed -n）。
{% else %}
output:
{{ text }}
{% endif %}

```

✅ 直觉：模板不仅是“插变量”，还能控制信息密度（尤其对成本与稳定性关键）。

---

## 例子 5：`format_error_template` —— 模型格式错了，系统用模板“纠正它”

### 场景：

你要求模型用“动作代码块”输出命令，但模型输出为普通文本：

```
我建议你运行 pytest -q
```

系统无法解析到命令（因为不符合预设格式），就会发一条纠错消息（由 `format_error_template` 渲染）回去。

### 纠错模板（示意）

```
你的输出无法解析为命令（错误：{{ error }}）。

请严格使用如下结构输出 bash 命令：

[动作代码块开始，语言标签=mswea_bash_command]
<命令>
[动作代码块结束]

```

### 模型下一轮就更可能改成（示例）

```
抱歉，我会按格式输出。

[动作代码块开始，语言标签=mswea_bash_command]
pytest -q
[动作代码块结束]

```

✅ 直觉：`format_error_template` 是“自动教练”，把模型拉回你规定的输出协议。

---

## 例子 6：模板 + 环境变量 `cwd` —— 解决“每步无状态，cd 不会保留”的坑

### 背景：

mini 的命令执行通常是“每步一个新 subshell”，所以你不能假设上一步的 `cd` 会影响下一步。

So，你可以把工作目录注入模板，要求模型每次命令都显式进入仓库根目录。

### `system_template`（示意）

```
所有 bash 命令必须在仓库根目录执行：{{ cwd }}

因此每条命令都必须写成：

[动作代码块开始，语言标签=mswea_bash_command]
cd {{ cwd }} && <命令>
[动作代码块结束]
```

### 模型输出会更像这样（示例）

```
[动作代码块开始，语言标签=mswea_bash_command]
cd /repo && pytest -q
[动作代码块结束]
```

✅ 直觉：模板可以把“环境事实/运行约束”变成硬规则，显著减少模型踩坑。

### PS：解释cwd：

- `{{ cwd }}` **不是凭空出现的**：它来自 **environment 配置变量**（例如你在 YAML 里写了 `environment: cwd: "/testbed"`），模板渲染时才会有这个变量可用。
- 在 V2 的模板系统里，environment 配置里写的字段会进入模板变量，例如 cwd、timeout 等。
- 一个最小的例子（Docker 环境）：

```bash
environment:
environment_class: docker
cwd: "/testbed"
timeout: 60
```

此时 {{ cwd }} 渲染出来就是 /testbed

**✅ 完整 YAML 片段（关键是补上 environment.cwd，并让示例和 cwd 一致）**

```bash
environment:
  environment_class: docker
  cwd: "/testbed"

agent:
  system_template: |
    所有 bash 命令必须在仓库根目录执行：{{ cwd }}
		[动作代码块开始，语言标签=mswea_bash_command]
		cd {{ cwd }} && <命令>
		[动作代码块结束]
```

---

## 一句话总结：模板到底是什么？

- `system_template`：全局规则 + 动作输出协议（最硬的约束）
- `instance_template`：注入任务与工作流（开局说明书）
- `observation_template`：执行结果如何呈现给模型（信息布局/截断/结构化）
- `format_error_template`：模型不按格式输出时如何纠正（自动教练）

---