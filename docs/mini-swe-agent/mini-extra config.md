# mini-extra config

下面把 **`mini-extra config`（或 `mini-e config`）** 讲清楚：

它就是一个“**全局 .env 配置文件**”的管理器，用来存你常用的默认模型名、API Key 等，让你不用每次运行都手动 export 环境变量。

你可以把它理解成：**“给 mini-swe-agent 用的 `git config --global`”**——写到一个全局配置文件里，所有项目都能读到。

---

# 它到底在管什么？

- 它管理的是一个**全局配置文件**（代码里叫 `global_config_file`），内容格式就是 `.env` 那种：
    
    ```
    MSWEA_MODEL_NAME=openai/gpt-5
    OPENAI_API_KEY=sk-xxxx
    MSWEA_CONFIGURED=true
    ```
    
- 这个文件的位置会在命令帮助里显示（脚本的 docstring 会打印路径）。你也可以用 `mini-extra config edit` 直接打开它。

---

# 最常用的 4 个命令

## 1) `setup`：交互式向导（第一次配置最省事）

```bash
mini-extra config setup
# 或
mini-e config setup
```

它会问你两类东西：

1. **默认模型名**（注意必须带 provider 前缀）
- ✅ `openai/gpt-5`
- ✅ `anthropic/claude-sonnet-4-5-20250929`
- ✅ `gemini/gemini-3-pro-preview`
- ❌ `gpt-5`（不带 `openai/` 不行）
1. **API Key 的“环境变量名 + 值”**
- 例如 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`

并且它会提示一句非常关键的话：

> 如果你已经在系统环境变量里设置了 API Key，就可以忽略这里的输入。
> 

也就是说：你可以只在全局配置里写模型名，把 key 留给系统环境变量管理。

---

## 2) `set`：直接写入某个键值（最精准、最好用）

### **例子 A：设置默认模型**

```bash
mini-extra config set MSWEA_MODEL_NAME openai/gpt-5
```

写完后，你的全局 `.env` 会多一行（或被更新）：

```
MSWEA_MODEL_NAME=openai/gpt-5
```

### **例子 B：设置 OpenAI Key**

```bash
mini-extra config set OPENAI_API_KEY sk-xxxxxxxx
```

结果：

```
OPENAI_API_KEY=sk-xxxxxxxx
```

### **例子 C：不带参数，进入交互式 set（适合记不清 key 名时）**

```bash
mini-extra config set
```

它会依次问：

- “Enter the key to set:”
- “Enter the value for :”

---

## 3) `unset`：删除某个键

### **例子：不想让默认模型固定了，删掉它**

```bash
mini-extra config unset MSWEA_MODEL_NAME

```

这会把 `.env` 里那行移除。

同理你也可以删掉某个 API key：

```bash
mini-extra config unset OPENAI_API_KEY

```

---

## 4) `edit`：用编辑器打开全局配置文件（适合批量改）

```bash
mini-extra config edit
```

它会用 `$EDITOR` 打开；如果你没设置 `$EDITOR`，就默认用 `nano`。

### **例子：用 VS Code 打开**

```bash
export EDITOR=code
mini-extra config edit
```

### **例子：用 vim 打开**

```bash
export EDITOR=vim
mini-extra config edit
```

---

# 关键配置项都有什么？（结合脚本看清楚）

从脚本可见，最核心的就是这几个：

## `MSWEA_MODEL_NAME`

- 你的**默认模型**
- 例：`anthropic/claude-sonnet-4-5-20250929` 或 `openai/gpt-5-mini`

## `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`

- 你选择的 provider 对应的 key
- `setup` 只会让你输入“一个 key”，但你完全可以用 `set` 再补多个 provider 的 key（比如你经常切换模型供应商）。

## `MSWEA_CONFIGURED`

- 是否已完成过 setup
- `setup` 最后一定会写：
    
    ```
    MSWEA_CONFIGURED=true
    ```
    
- 这个值用于 `configure_if_first_time()` 判断“要不要自动弹出 setup 向导”。

---

# 真实工作流示例（很实用）

## 示例 1：第一次装好工具，快速可用（推荐）

1. 跑向导：

```bash
mini-extra config setup
```

1. 选择：
- 默认模型：`openai/gpt-5-mini`
- key 名：`OPENAI_API_KEY`
- key 值：填你的 key

之后你基本就“开箱即用”，不需要每次 export。

---

## 示例 2：我不想把 key 写进文件，只想用系统环境变量

你可以这样做：

1. `setup` 时只填模型名，key 那块直接回车跳过
2. 在 shell 配置（比如 `~/.zshrc`）里放：

```bash
export OPENAI_API_KEY="sk-xxxx"
```

好处：

- 更符合一些团队安全习惯（key 不落地到配置文件）
- CI / 服务器部署也更常见（由环境变量注入）

---

## 示例 3：经常切模型供应商（OpenAI / Anthropic 来回切）

你可以一次把多个 key 都放进全局 `.env`：

```bash
mini-extra config set OPENAI_API_KEY sk-openai
mini-extra config set ANTHROPIC_API_KEY sk-anthropic
```

想切默认模型就改这一项：

```bash
mini-extra config set MSWEA_MODEL_NAME anthropic/claude-sonnet-4-5-20250929
# 或切回
mini-extra config set MSWEA_MODEL_NAME openai/gpt-5
```

---

# 它背后到底怎么改文件的？（通俗解释）

- `set` / `setup` 用的是 `python-dotenv` 的 `set_key()`：
    
    相当于“**在 .env 里写入/更新一行 KEY=VALUE**”
    
- `unset` 用的是 `unset_key()`：
    
    相当于“**把那行删掉**”
    
- `edit` 就是：
    
    ```python
    subprocess.run([editor, global_config_file])
    ```
    
    直接打开那个文件手动改。
    

---

# 小提醒（避免踩坑）

1. **模型名一定要带 provider 前缀**
    
    例如 `openai/gpt-5`，不要写成 `gpt-5`
    
2. **setup 不是必须把 key 填进去**
    
    你可以完全依赖系统环境变量
    
3. **这是“全局配置”**
    
    改了会影响你所有使用同一套全局配置的项目/运行（这正是它的价值）
    

---

# 如何使用.env来管理不同项目的环境变量

可以的——**但要分清两层“变量”**，以及一个关键事实：

**你贴的这份 `mini` 源码本身并不会自动读取 `.env` 文件**（没有 `python-dotenv` 之类的加载逻辑）。

所以“每个项目各自 `.env` 控制变量”是可行的，但需要你用 **shell/工具把 `.env` 里的变量导入到运行 `mini` 的进程环境里**，或者把变量放进各自的 `mini.yaml` 配置中。

---

# 1) 你想控制的变量大致有两类

### A. 影响 `mini` 启动/配置选择的变量（在 `mini` 进程启动前就要确定）

典型就是你文档里提到的：

- `MSWEA_MINI_CONFIG_PATH`：默认用哪个 config
- `MSWEA_MODEL_NAME`：默认用哪个模型

✅ 这些**非常适合用“每个项目一个 `.env`”来管理**。

### B. 影响 agent 执行的命令环境的变量（比如每次 `bash` 命令执行时的环境）

- 你给的默认配置里就有 `environment.env: { PAGER: cat, ... }` 这类字段

✅ 这类更推荐写进**项目自己的 `mini.yaml`**（因为它会稳定地影响 agent 的每次命令执行环境）。

---

# 2) “不同项目同时用各自 `.env`”是否可行？

- 可行。核心原因是：环境变量是“进程级/终端会话级”的。
- 你在 **Project A 的终端**里加载 A 的 `.env` 再跑 `mini` → 该 `mini` 进程继承 A 的变量
- 你在 **Project B 的另一个终端**里加载 B 的 `.env` 再跑 `mini` → 另一个 `mini` 进程继承 B 的变量

两边互不影响，所以可以“同时”。

⚠️ 但在**同一个终端会话**里，环境变量只有一份，你要切项目就需要重新加载/覆盖。

---

# 3) 实际做法（最简单、最常用的 4 种）

## 做法 1：每次运行前 `source .env`（最直观）

在项目目录里放一个 `.env`：

**ProjectA/.env**

```bash
MSWEA_MODEL_NAME=gpt-5
MSWEA_MINI_CONFIG_PATH=./mini.yaml
```

然后运行：

```bash
set -a; source .env; set +a
mini -t "修复一个小问题"
```

- `set -a` 会让 `.env` 里定义的变量自动 export（否则 `source` 只是 shell 变量，不一定传给子进程）
- 这样 `mini` 就能读到 `MSWEA_*` 变量

> 同时开两个终端分别在 A/B 目录这样做，就是“并行不同项目不同 .env”。
> 

关键在于 **`source .env` 并不是“读文件”，而是“把文件里的每一行当成你在当前终端里手动敲的命令来执行”**。而 `set -a` 会改变“执行赋值语句时”变量是否自动带上 `export` 标记。

---

### 做法 1.1) `source .env` 到底做了什么？

假设你的 `.env` 是这样：

```bash
MSWEA_MODEL_NAME=gpt-5
MSWEA_MINI_CONFIG_PATH=./mini.yaml
```

当你执行：

```bash
source .env
```

等价于你在当前 shell 里**逐行手动输入**：

```bash
MSWEA_MODEL_NAME=gpt-5
MSWEA_MINI_CONFIG_PATH=./mini.yaml
```

也就是说：它只是创建/更新了两个 **shell 变量**。

⚠️ 但**shell 变量**默认不会自动传给你之后启动的程序（子进程）。
要传给子进程，必须是 **环境变量（exported variable）**。

---

### 做法 1.2) “环境变量” vs “普通 shell 变量”

- 普通 shell 变量：只在当前 shell 有效（子进程看不到）
    
    ```bash
    FOO=bar
    ```
    
- 环境变量：会被子进程继承（比如 `mini`）
    
    ```bash
    export FOO=bar
    ```
    

所以如果你只做：

```bash
source .env
mini ...
```

多数情况下 `mini` 可能拿不到（取决于 `.env` 有没有写 `export`）。

---

### 做法 1.3) `set -a` 的真正逻辑：让“赋值语句自动 export”

`set -a` 会开启 bash/zsh 的一个开关叫 **allexport**。

开启后，**任何变量赋值**都会被当作“自动 export”。

也就是说：

```bash
set -a
FOO=bar
```

在开启 `set -a` 的情况下，效果等价于：

```bash
export FOO=bar
```

所以：

```bash
set -a
source .env
set +a
```

这三句连起来的意思是：

1. `set -a`：从现在起，所有赋值都自动 export
2. `source .env`：执行 `.env` 里的赋值（因为开了 allexport，所以这些赋值自动变成环境变量）
3. `set +a`：关掉 allexport，避免后续你随手写的变量也被自动 export（防污染）

**重点：自动 export 不是 `source` 做的，是 `set -a` 改变了“赋值”的行为。**`source` 只是“执行文件里的赋值”。

---

### 做法 1.4) 用一个小实验验证你就会完全懂

你可以在终端里这样做（不需要真的跑 mini）：

```bash
unset FOO

source <(printf 'FOO=bar\\n')
env | grep '^FOO=' || echo "FOO 不在环境变量里"

set -a
source <(printf 'FOO=bar\\n')
set +a
env | grep '^FOO=' || echo "FOO 不在环境变量里"
```

你会看到：

- 第一段：`FOO=bar` 只是在 shell 里，不在 `env` 输出里
- 第二段：开启 `set -a` 后，`FOO=bar` 会出现在 `env` 里（说明它被 export 了）

---

### 做法 1.5) `.env` 文件要长什么样才适合 `source`？

适合（纯 bash 赋值语法）：

```bash
A=1
B="hello world"
C=/some/path
# 注释可以
```

不太适合（常见坑）：

- 有空格但没引号：`B=hello world`（这会被当成命令参数）
- 不是 bash 语法的行（比如某些 dotenv 风格支持的奇怪写法）
- 需要转义但没转义

如果你的 `.env` 是标准的 “KEY=VALUE” 且符合 shell 语法，`source` 就很稳。

---

一句话记忆法：

**`source .env` = 在当前终端执行里面的赋值；`set -a` = 让这些赋值自动变成 `export`，因此后面启动的 `mini` 能继承它们。**

---

## 做法 2：用“命令前缀”一次性指定（不污染当前终端环境）

```bash
MSWEA_MODEL_NAME=gpt-5 \
MSWEA_MINI_CONFIG_PATH=./mini.yaml \
mini -t "修复一个小问题"
```

优点：不会把环境变量留在当前 shell 里，干净。

---

## 做法 3：用 `direnv`（强烈推荐：项目级自动切换）

### 场景：你有两个项目

- `~/work/projA`
- `~/work/projB`

你想做到：

1. 进入 `projA` 时：默认用 `gpt-5`、用 A 的 `mini.yaml`
2. 进入 `projB` 时：默认用另一个模型/配置
3. 同时每个项目的 agent 命令执行时，都有各自的 `PYTHONPATH` 等环境（写在 `mini.yaml` 里，更稳）

---

### **用 direnv：进入目录自动 export，离开自动恢复**

### 做法 3.1) 在 projA 下创建两个文件

`~/work/projA/.env`

```bash
MSWEA_MODEL_NAME=gpt-5
MSWEA_MINI_CONFIG_PATH=./mini.yaml
```

`~/work/projA/.envrc`

```bash
dotenv
```

> dotenv 这句的含义：让 direnv 读取同目录的 .env，并把里面的变量 export 到当前 shell。
> 

### 做法 3.2) 在 projB 下也创建

`~/work/projB/.env`

```bash
MSWEA_MODEL_NAME=gpt-5-mini
MSWEA_MINI_CONFIG_PATH=./mini.yaml
```

`~/work/projB/.envrc`

```bash
dotenv
```

### 做法 3.3) 第一次要“授权” direnv 执行 `.envrc`

在每个项目目录里各执行一次：

```bash
cd ~/work/projA
direnv allow
```

```bash
cd ~/work/projB
direnv allow
```

### 做法 3.4) 现在你会看到“自动切换”的效果（最关键）

**终端 1：**

```bash
cd ~/work/projA
echo $MSWEA_MODEL_NAME
# 期望输出：gpt-5
```

**终端 2：**

```bash
cd ~/work/projB
echo $MSWEA_MODEL_NAME
# 期望输出：gpt-5-mini
```

这就解释了“项目级自动切换”：你只要 `cd`，环境变量就自动变了；离开目录（cd 到别处）就自动恢复。

### 做法 3.5) 直接跑 mini（不需要手动 `export` / `source`）

在 projA：

```bash
mini -t "打印当前模型和配置路径"
```

在 projB：

```bash
mini -t "打印当前模型和配置路径"
```

**因为 direnv 已经帮你 export 进当前 shell 了，所以 `mini` 能读到 `MSWEA_MODEL_NAME` 和 `MSWEA_MINI_CONFIG_PATH`。**

---

## 做法 4：把“可重复的环境”写进各自 `mini.yaml`（更稳、更可控）

### 场景：你有两个项目

- `~/work/projA`
- `~/work/projB`

你想做到：

1. 进入 `projA` 时：默认用 `gpt-5`、用 A 的 `mini.yaml`
2. 进入 `projB` 时：默认用另一个模型/配置
3. 同时每个项目的 agent 命令执行时，都有各自的 `PYTHONPATH` 等环境（写在 `mini.yaml` 里，更稳）

---

### 用 mini.yaml：控制“agent 执行命令”时的环境（更稳）

这部分解决的是：**就算你没正确加载 `.env`，agent 跑命令时也能稳定拿到环境变量**（而且适合团队协作、可版本管理）。

### projA 的 `mini.yaml`

`~/work/projA/mini.yaml`

```yaml
environment:
  env:
    PYTHONPATH: "./src"
    MY_PROJECT: "A"
```

### projB 的 `mini.yaml`

`~/work/projB/mini.yaml`

```yaml
environment:
  env:
    PYTHONPATH: "./app"
    MY_PROJECT: "B"
```

### 验证“这个 env 会影响 agent 的每次命令执行”

在 projA 运行：

```bash
mini -c ./mini.yaml -t "执行 bash 命令：echo $MY_PROJECT && python -c 'import sys; print(sys.path[:3])'"
```

你会看到它提出要执行类似命令（confirm/yolo 看你模式），输出应该包含：

- `MY_PROJECT` 相关的值（A）
- Python 的 `sys.path` 里出现 `./src`（或对应路径）

在 projB 同理会输出 B，并且路径不同。

> 这说明：mini.yaml -> environment.env 是控制“agent 运行命令的环境”的，不依赖你外部 shell 有没有 export。
> 

---

# 4) 一个你需要注意的点：agent 命令是“每次新 subshell”

你贴的 system template 里明确写了：

> Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
> 

这意味着：如果你在某一步命令里 `export FOO=bar`，**下一步不一定还在**。

所以如果你希望某个变量对“每一步命令”都生效，更推荐：

- 放进 `mini.yaml` 的 `environment.env`
- 或者每条命令都前缀 `FOO=bar ...`
- 或者每条命令都 `source .env && ...`（比较笨但有效）

---

# 推荐组合（现实里最顺手的）

- **团队共享/可复现的变量**：写进项目的 `mini.yaml`
- **个人机器/私密 token / 本地差异**：写进项目 `.env`，用 `direnv` 或 `source .env` 注入

**一句话：可复现配置写 mini.yaml，敏感/私密 key 用 direnv 的 .env 管理。**

---

最推荐的“清晰分工”：

- **direnv + .env**：控制“启动 mini 本身要用的变量”
    - 比如 `MSWEA_MODEL_NAME`、`MSWEA_MINI_CONFIG_PATH`
- **mini.yaml**：控制“agent 执行 bash 命令时的环境”
    - 比如 `PYTHONPATH`、项目开关、工具路径等

这样你在多个项目间切换时就非常顺滑：`cd` 即切换，团队也能复现。