# Quick start

---

# 1) 安装 uv（只做一次）

**macOS / Linux：**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version

```

([Astral 文档](https://docs.astral.sh/uv/getting-started/installation/?utm_source=chatgpt.com))

（Windows 也有对应安装方式；官方文档同页里有 PowerShell 安装说明。）([Astral 文档](https://docs.astral.sh/uv/getting-started/installation/?utm_source=chatgpt.com))

---

# 2) 不安装到系统：用 uvx 直接跑 mini（最省事）

`uvx` 会把工具装到**匿名隔离环境**里跑（类似 pipx / npx），非常适合先试用。([Mini Swe Agent](https://mini-swe-agent.com/latest/quickstart/))

## 2.1 直接启动 CLI

```bash
uvx mini-swe-agent

```

([Mini Swe Agent](https://mini-swe-agent.com/latest/quickstart/))

## 2.2 运行额外工具（比如配置向导）

```bash
uvx --from mini-swe-agent mini-extra config setup

```

([Mini Swe Agent](https://mini-swe-agent.com/latest/quickstart/))

> 说明：mini 是主 CLI；mini-extra 是一些辅助命令（配置、工具等）。(Mini Swe Agent)
> 

## 2.3 避免“用到旧缓存版本”

`uvx` 会缓存下载的版本；如果你怀疑没更新到最新，通常用 `--refresh` 更合适。([Astral 文档](https://docs.astral.sh/uv/concepts/tools/?utm_source=chatgpt.com))

例如：

```bash
uvx --refresh mini-swe-agent

```

---

# 3) 第一次运行必做：配置模型与 API key

第一次跑 `mini` 会引导你做模型设置；如果你跳过了，直接跑：

```bash
mini-extra config setup

```

([Mini Swe Agent](https://mini-swe-agent.com/latest/quickstart/))

mini 的全局配置支持两种方式：

- **环境变量**
- 或写入 `.env`（`mini` 启动时会打印出 `.env` 的具体路径）
    
    并且：**环境变量优先于 `.env`**。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/global_configuration/))
    

## 常用配置命令（推荐你记住这三个）

```bash
mini-extra config set KEY VALUE
mini-extra config unset KEY
mini-extra config edit

```

([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/global_configuration/))

比如设置默认模型与 key（示例）：

```bash
mini-extra config set MSWEA_MODEL_NAME "anthropic/claude-sonnet-4-5-20250929"
mini-extra config set ANTHROPIC_API_KEY "sk-..."

```

([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/global_configuration/))

官方推荐模型（供你直接抄）：

- `anthropic/claude-sonnet-4-5-20250929`
- `openai/gpt-5` 或 `openai/gpt-5-mini`
    
    ([Mini Swe Agent](https://mini-swe-agent.com/latest/quickstart/))
    

---

# 4) 在你的代码仓库里使用 mini（最常见工作流）

进入你的项目根目录，然后：

## 4.1 交互式让它干活（会提示你输入任务）

```bash
mini
```

([Mini Swe Agent](https://mini-swe-agent.com/latest/quickstart/))

## 4.2 直接给任务（更快）

```bash
mini -t "Please run pytest on the current project, discover failing unittests and help me fix them. Always make sure to test the final solution."
```

([Mini Swe Agent](https://mini-swe-agent.com/latest/quickstart/))

## 4.3 常用参数（你大概率会用到）

- 指定任务：`t/--task`
- 指定配置文件：`c/--config`（默认会找 `mini.yaml`，或用 `MSWEA_MINI_CONFIG_PATH`）
- 临时指定模型：`m/--model`（否则用 `MSWEA_MODEL_NAME`）
- 直接 yolo 模式：`y/--yolo`（不再逐步确认）
    
    ([Mini Swe Agent](https://mini-swe-agent.com/latest/usage/mini/))
    

---

# 5) 长期使用：把 mini 安装成系统命令（推荐）

如果你确认要常用，装成“工具”最舒服：

```bash
uv tool install mini-swe-agent
mini
mini-extra
```

([Mini Swe Agent](https://mini-swe-agent.com/latest/quickstart/))

### 升级（已安装的工具）

```bash
uv tool upgrade mini-swe-agent
```

([Astral 文档](https://docs.astral.sh/uv/guides/tools/?utm_source=chatgpt.com))

---

# 6) 关于 v2（避免踩坑）

官方明确提示 **v2.0 将有 breaking changes**；如果你想暂时留在当前大版本，可以“固定版本范围”。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/v2_migration/?utm_source=chatgpt.com))

例如（概念示例）：

```bash
uv tool install "mini-swe-agent~=1.0"

```

（`uv tool install` 支持带版本约束的安装方式。）([Astral 文档](https://docs.astral.sh/uv/guides/tools/?utm_source=chatgpt.com))

---