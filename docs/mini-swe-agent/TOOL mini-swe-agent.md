# TOOL: mini-swe-agent

**mini-swe-agent 本身并不“需要” tool-calling 或 MCP tools 才能工作**；它是刻意做成 **bash-only** 的。([mini-swe-agent.com](https://mini-swe-agent.com/latest/faq/?utm_source=chatgpt.com))

但如果你的目标是“让它像 Claude Desktop / Claude Code 那样连一堆外部工具（MCP server）”，也有几种**可行且成本不同**的解决办法（从几乎不改代码到改框架都有）。

---

# 1) mini-swe-agent 到底“需不需要” tool / MCP？

## 不需要（允许你完全不用）

官方定位非常明确：**它没有除 bash 以外的工具，甚至不依赖模型的 tool-calling 接口**；通过解析模型输出并执行 shell 命令形成闭环。([mini-swe-agent.com](https://mini-swe-agent.com/latest/faq/?utm_source=chatgpt.com))

SWE-bench Bash Only 也强调：mini-swe-agent 用于“最小 bash 环境”，不靠额外工具脚手架，做更公平的模型对比。([SWE-bench](https://www.swebench.com/bash-only.html?utm_source=chatgpt.com))

---

# 2) 那如果我“想要/必须要” tool 或 MCP，该怎么做？

先统一一下概念：

- *MCP（Model Context Protocol）**是一个开放协议，用于把 LLM 应用连接到外部数据源/工具（通过 MCP servers 暴露 tools/resources/prompts）。([Anthropic](https://www.anthropic.com/news/model-context-protocol?utm_source=chatgpt.com))

它的生态里有官方 SDK（含 Python SDK），也有现成 CLI 客户端。([GitHub](https://github.com/modelcontextprotocol/python-sdk?utm_source=chatgpt.com))

下面按“侵入性/改造量”从小到大给你 3 条主路线：

---

## 方案 A（推荐）：**把 MCP 工具“命令行化”，仍然走 bash-only**

### 核心思路

mini-swe-agent 只会执行 bash——那就把你想用的 MCP tool 变成一个可执行命令：

- 要么用现成的 MCP CLI
- 要么写一个很薄的 wrapper（Python/Node）去调用 MCP server
    
    然后让模型在 bash 里调用这个命令即可。
    

### 为什么它可行？

因为 mini-swe-agent 的工具面本来就是 bash，你提供一个 `mcp-cli …` 命令，等价于给它“新增了能力”，但**不需要它支持原生 tool-calling**。([mini-swe-agent.com](https://mini-swe-agent.com/latest/faq/?utm_source=chatgpt.com))

### 你可以直接用现成的 MCP CLI

例如 `mcp-cli`（PyPI 上的 MCP 命令行客户端）提供：

- 列出工具：`mcp-cli tools ...`
- 直接执行工具：`mcp-cli cmd --server <...> --tool <...> ...` ([PyPI](https://pypi.org/project/mcp-cli/))

### 让模型这样用（示例）

你可以在系统提示词里加一条规则：

> “当需要访问外部系统（例如数据库/Jira/内部服务）时，优先使用 mcp-cli 调用 MCP server 的工具；把输出当作 JSON 结果继续推理。”
> 

模型可能就会输出类似（仍然是 bash）：

```bash
mcp-cli tools --server sqlite

```

或：

```bash
mcp-cli cmd --server sqlite --tool list_tables --output tables.json
cat tables.json

```

> 优点：几乎零改 mini-swe-agent 代码。
> 
> 
> 代价：你的运行环境里需要能安装/运行 `mcp-cli`，以及 MCP servers（可能还需要网络/本地权限配置）。
> 

---

## 方案 B：**写一个“超薄 MCP 客户端脚本”，当作你的专用工具命令**

如果你不想引入一个“大 CLI”，或者你要做企业内定制（鉴权/审计/参数校验），可以用官方 SDK 写一个几十行脚本：

- MCP 官方文档提供“如何构建 MCP client”教程，且支持 Python/TS/Java 等。([模型上下文协议](https://modelcontextprotocol.io/docs/develop/build-client?utm_source=chatgpt.com))
- 官方也有 Python SDK 仓库。([GitHub](https://github.com/modelcontextprotocol/python-sdk?utm_source=chatgpt.com))

你最终得到一个命令，比如：

```bash
mcp_call --server jira --tool search_issues --args '{"jql":"..."}'

```

mini-swe-agent 仍只是在执行 bash，但你已经“间接拥有 MCP 工具生态”。

> 优点：更轻、更可控（可以把输出格式固定成模型更好消费的 JSON）。
> 
> 
> 代价：要自己维护这个脚本 + 运行依赖。
> 

---

## 方案 C（中等改造）：**让 mini-swe-agent 真的“支持多工具”或 MCP 调用**

这条路线的意思是：你不满足于“bash 调一个 CLI”，而是希望像原生 tool-calling 那样，模型输出结构化 `MCP_CALL`，agent 负责路由执行。

mini-swe-agent 的架构使得这并不难（它很小），但会偏离它的设计哲学（bash-only baseline）：

- 你可以在 YAML 里改 action 的解析规则（比如增加一个新的 block/regex），让模型输出两种 action：`BASH` 和 `MCP_CALL`。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/?utm_source=chatgpt.com))
- 然后在 `execute_actions` 里新增分发逻辑：遇到 `MCP_CALL` 就用 MCP Python SDK 去调用 server，返回结果追加到 history。

> 优点：工具调用更结构化，模型更不容易“把命令写坏”。
> 
> 
> 代价：你开始维护一个“工具系统”，mini 的简洁优势会被稀释；同时你也会失去一部分“bash-only 可比性”（尤其用于基准评测时）。([SWE-bench](https://www.swebench.com/bash-only.html?utm_source=chatgpt.com))
> 

---

## 方案 D（换框架）：如果你“就是要工具体系”，考虑用更工具化的 Agent

如果你的需求核心就是“工具插件体系 / 丰富工具 / 更复杂编排”，那更自然的选择是用 **SWE-agent** 那类强调工具的框架（它的定位就是让模型用工具在真实仓库里修 issue）。([SWE Agent](https://swe-agent.com/?utm_source=chatgpt.com))

或者用更 MCP-native 的 agent 框架，把 MCP 当一等公民来设计（例如一些专门围绕 MCP servers 的 agent 框架）。([GitHub](https://github.com/lastmile-ai/mcp-agent?utm_source=chatgpt.com))

---

# 3) 什么时候“确实值得上 MCP”？

当你遇到这些情况，MCP 往往比“让模型直接 curl/写脚本”更合适：

1. **企业系统很多且鉴权复杂**：希望用统一协议接入（工具 schema、权限边界、审计）。([Anthropic](https://www.anthropic.com/news/model-context-protocol?utm_source=chatgpt.com))
2. **你想复用现成 MCP servers 生态**（数据库、文件系统、Git、SaaS 等）。([GitHub](https://github.com/modelcontextprotocol/servers?utm_source=chatgpt.com))
3. **你希望工具接口强约束**（schema 校验、参数类型、结果结构），减少“模型乱拼命令”的概率。([模型上下文协议](https://modelcontextprotocol.io/docs/sdk?utm_source=chatgpt.com))

---

# 4) 安全与运维提醒（很关键）

把外部工具接进 agent，攻击面会显著增大。近期就出现过与 MCP server 相关的安全事件报道（例如 Git 相关 MCP server 的漏洞修复与潜在风险）。([TechRadar](https://www.techradar.com/pro/security/anthropics-official-git-mcp-server-had-some-worrying-security-flaws-this-is-what-happened-next?utm_source=chatgpt.com))

工程上建议你至少做到：

- MCP server 最小权限（只给必要目录/必要 API scope）
- 沙箱隔离（容器/只读挂载/网络白名单）
- 对“工具输出 → 再执行”的链路做审计与限制（避免 prompt injection 诱导执行危险命令）

---

# 5) 我给你的“最实用建议”（按目标选）

- 你想保持 mini-swe-agent 的风格、又想连 MCP：**选方案 A（bash 调 MCP CLI）**
- 你需要企业定制/更轻量：**选方案 B（自写小 wrapper 命令）**
- 你要“结构化工具路由”并愿意改代码：**选方案 C**
- 你本质需求是“强工具框架”：**选方案 D**

---