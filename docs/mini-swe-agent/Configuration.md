# Configuration

# mini-SWE-agent 里到底配什么、配在哪里？

mini-SWE-agent 的“配置”主要分两层：

- **全局配置（Global configuration）**：用 **环境变量 / `.env`** 管理“跨所有运行都通用”的东西（API key、默认模型、全局预算、默认配置文件路径等）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))
- **运行配置（YAML config files）**：用 **YAML** 管理“这次/这类 agent 怎么跑”（提示词模板、step/cost 限制、环境类型 docker/local、action 解析规则等）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

# Global configuration（环境变量 / `.env`）怎么用

## 1) 适合放什么

全局配置面向的是“工具级别”的默认行为，比如：

- **默认模型**、**各家模型 API Key**（Anthropic/OpenRouter/…）
- **全局成本/调用次数上限**（避免不小心烧钱）
- **默认配置文件的搜索路径**（比如你有一套自己的 config）
- **docker/singularity/bwrap 可执行文件路径**（多环境适配）
- inspector 的样式文件路径等([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

> 通俗理解：**全局配置 = 这台机器/这个用户的默认偏好**；YAML = 这次任务/这类任务的“剧本”。
> 

---

## 2) 怎么设置 + 优先级是什么

### 优先级（非常关键）

- **环境变量 > `.env` 文件**（环境变量会覆盖 `.env` 里的同名设置）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

### 常用设置方式

**方式 A：用 `mini-extra`（更省心，适合“永久默认值”）**

```bash
mini-extra config setup                       # 交互式引导：模型+key 等
mini-extra config set MSWEA_MODEL_NAME "anthropic/claude-sonnet-4-5-20250929"
mini-extra config set ANTHROPIC_API_KEY "sk-..."
mini-extra config unset ANTHROPIC_API_KEY
mini-extra config edit                        # 直接打开 .env 编辑
```

这些命令就是在帮你维护 `.env`。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

**方式 B：直接 export（适合“临时试验/一次性覆盖”）**

```bash
export MSWEA_MODEL_NAME="anthropic/claude-sonnet-4-5-20250929"
export MSWEA_GLOBAL_COST_LIMIT="5.00"
mini
```

文档也明确建议：

**临时实验或 API key** 可以用环境变量。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

---

## 3) 常见全局变量怎么用（按用途分组）

### A. 模型与 Key

- `MSWEA_MODEL_NAME`：默认模型名（如果 CLI 没有 `m` 覆盖）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))
- `LITELLM_MODEL_REGISTRY_PATH`：给 litellm 注册额外模型（本地/自建端点常用）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

**例：只改默认模型**

```bash
mini-extra config set MSWEA_MODEL_NAME "anthropic/claude-sonnet-4-5-20250929"
```

([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

### B. 成本与重试（强烈建议配上）

- `MSWEA_GLOBAL_CALL_LIMIT`：全局最大模型调用次数（0=不限制）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))
- `MSWEA_GLOBAL_COST_LIMIT`：全局最大美元花费（0=不限制）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))
- `MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT`：API 调用失败时的重试次数上限。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))
- `MSWEA_COST_TRACKING="ignore_errors"`：忽略 cost tracking 错误（**可能导致支出失控**，文档有强提醒）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

**例：给自己加“安全带”**

```bash
mini-extra config set MSWEA_GLOBAL_COST_LIMIT "10.00"
mini-extra config set MSWEA_GLOBAL_CALL_LIMIT "100"
```

([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

### C. 配置文件搜索与默认入口

- `MSWEA_CONFIG_DIR`：额外的配置目录（让你可以只写 `c my.yaml`，不用写完整路径）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))
- `MSWEA_MINI_CONFIG_PATH`：`mini` 的默认配置文件路径（不设则用内置 `mini.yaml`）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))
- `MSWEA_DEFAULT_RUN`：主 CLI 的默认 run script 入口点。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

**例：把你的 configs 目录加入搜索路径**

```bash
mini-extra config set MSWEA_CONFIG_DIR "/path/to/my/configs"
# 之后：mini -c my-mini.yaml  就能找到 /path/to/my/configs/my-mini.yaml
```

([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

### D. 环境后端可执行文件路径

- `MSWEA_DOCKER_EXECUTABLE` / `MSWEA_SINGULARITY_EXECUTABLE` / `MSWEA_BUBBLEWRAP_EXECUTABLE`：你系统里这些命令不在默认 PATH 或名字不同的时候用。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

---

# YAML configuration（agent 配置文件）怎么用

## 1) YAML 的整体结构（你主要在改这四块）

YAML 顶层通常是这几个 key：`agent` / `environment` / `model` / `run`。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

一个最小化示意（只展示“你会常改的”）：

```yaml
agent:
  mode: confirm
  cost_limit: 3.0

environment:
  environment_class: local
  cwd: "/testbed"

model:
  model_name: "anthropic/claude-sonnet-4-5-20250929"
```

---

## 2) `agent`：控制“它怎么思考/怎么走流程/怎么收敛”

你最常动的是：

- `system_template` / `instance_template`：提示词模板（Jinja2 渲染）
- `step_limit` / `cost_limit`：步数/成本限制
- `mode`：confirm / yolo 等（是否每步要你确认）([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

### 模板是 Jinja2：`{{task}}` 只是开始

YAML 里模板用 **Jinja2**，可以插变量、写条件逻辑。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

内置 agent 的模板里通常能用到**这些变量**（很实用）：

- 环境变量（仅 LocalEnvironment）
- `agent` / `environment` 配置里的字段
- `run(...)` 传入的变量（默认就有 `task`）
- 上一次 action 执行的输出对象 `output`([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

**例：把任务写进提示词**

```yaml
agent:
  instance_template: |
    请解决这个任务：
    {{task}}
    你需要先定位相关文件，再复现，再修复，再验证。
```

---

## 3) `environment`：控制“命令在哪跑、以什么方式跑”

常用项包括：

- `environment_class`：`local` / `docker` / `singularity` 等
- `cwd`、`timeout`、`env`（给子 shell 的环境变量）([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

**例：统一把 pager 关掉，避免输出分页**

```yaml
environment:
  env:
    PAGER: cat
    MANPAGER: cat
    LESS: -R
```

（这也是默认示例里常见写法）([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

## 4) `model`：控制“用哪个模型 + 怎么解析/呈现 action 与 observation”

常见项：

- `model_name` / `model_class`
- `observation_template`：把命令输出怎么塞回模型上下文（常用来截断超长输出）
- `action_regex`：如果你不用 tool-calls，而是让模型在文本里输出命令块，用它来“抓命令”([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

### 自定义 action 输出格式（Markdown ➜ XML）的关键点

文档强调：

**如果你改了 `action_regex`（比如用 XML tag），system_template / instance_template / format_error_template 等所有模板都要统一同一种格式，否则模型会输出 A 格式但解析器按 B 格式抓，直接跑不起来。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))**

**例：用 XML 包一层命令**

```yaml
model:
  action_regex: <mswea_bash_command>(.*?)</mswea_bash_command>
agent:
  system_template: |
    你必须用：
    <mswea_bash_command>your_command</mswea_bash_command>
```

([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

## YAML 里写 regex 的坑：别用 `|` 把 regex 变多一行

`action_regex` **最好保持单行**、**不要加引号**；

用 `|` 可能会在末尾带一个换行，导致 regex 不符合预期。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---

# CLI 运行时：全局配置 + YAML 是怎么“合并生效”的（实现原理视角）

## 1) 默认会加载哪个 YAML？

`mini` 的默认 config 文件来自：

- 环境变量 `MSWEA_MINI_CONFIG_PATH`（如果设置了）
- 否则就是内置的 `mini.yaml`([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/mini/))

## 2) `c/--config` 的行为（非常实用）

实现里 `-c/--config` 接受的是 **config_spec 列表**，并且：

- **只要你显式传了 `c`，默认 config 就不会自动再加进来**
    
    所以如果你写 `-c model.model_kwargs.temperature=0`，你其实把整套默认提示词/环境配置都丢了（help 文本专门提醒）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/mini/))
    
- 你可以传多个 `c`，它们会 **递归 merge**（后面的覆盖前面的同路径字段）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/mini/))
- `c` 既可以是 **文件名/路径**，也可以是 **key-value 覆盖**（如 `model.model_kwargs.temperature=0.5`）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/mini/))

### **例：在默认 mini.yaml 基础上，只把 temperature 改成 0.5**

```bash
mini -c mini.yaml -c model.model_kwargs.temperature=0.5
```

([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/mini/))

---

# 4 个“很实际”的配置小配方（照抄就能用）

## 1) 临时换模型跑一次（不污染你的默认配置）

```bash
MSWEA_MODEL_NAME="anthropic/claude-sonnet-4-5-20250929" mini
```

（环境变量会覆盖 `.env`）([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

## 2) 永久设置默认模型 + Key（以后每次直接 mini）

```bash
mini-extra config set MSWEA_MODEL_NAME "anthropic/claude-sonnet-4-5-20250929"
mini-extra config set ANTHROPIC_API_KEY "sk-..."
```

([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/global_configuration/))

## 3) 给“某类任务”单独一份 YAML（更严格流程 + 更小预算）

`my-strict.yaml`：

```yaml
agent:
  mode: confirm
  cost_limit: 1.0
  instance_template: |
    任务：{{task}}
    你必须：定位 -> 复现 -> 修复 -> 验证。
```

运行：

```bash
mini -c mini.yaml -c my-strict.yaml -t "把 a.py 里的 bug 修了"
```

（多 `-c` 会 merge）([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/mini/))

## 4) 改 action 输出格式（Markdown 命令块 ➜ XML 标签）

```yaml
model:
  action_regex: <mswea_bash_command>(.*?)</mswea_bash_command>
agent:
  system_template: |
    你必须用 <mswea_bash_command>...</mswea_bash_command> 输出命令
```

并确保所有模板都一致（否则解析不到）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/advanced/yaml_configuration/))

---