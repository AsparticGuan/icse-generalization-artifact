# 启动: mini

---

# 1) mini 是什么？你用它来干嘛？

**mini** 是一个在本地运行的、REPL 风格（像交互式终端）的命令行工具，用来驱动 **mini-SWE-agent** 在你的本机环境里做事（读文件、跑命令、改代码、验证等）。

你可以把它理解成：

- 你给它一个任务（task），比如“修复某个 bug / 改一个脚本 / 实现一个功能”
- 模型会一步步提出要执行的命令（比如 `ls`, `grep`, `pytest`）
- 你可以选择：
    - 让它每一步都问你“要不要执行？”（confirm）
    - 让它直接执行（yolo）
    - 你自己接管终端手动敲命令（human）

---

# 2) 最快上手：三种启动方式

## A. 直接给任务（最常用）

```bash
mini -t "修复 tests/test_x.py 里的失败用例，并确保所有测试通过"
```

这样它一启动就开始做事，不会再问你“你要做什么”。

## B. 不给任务，启动后再输入（交互式输入任务）

```bash
mini
```

启动后会出现：

```
What do you want to do?
```

此时你可以输入任务。它支持**多行输入**（底部提示里会告诉你提交方式：通常是 *Esc 然后 Enter*）。

## C. 一上来就 YOLO（谨慎）

```bash
mini -y -t "重构 src/xxx.py，让函数更清晰，并补测试"
```

- `y/--yolo` 会让它开局就是“直接执行命令不再确认”。

---

# 3) 命令行参数：你最需要记的几个

## `t/--task`

指定任务文本；不写就启动后提示你输入。

**例子**

```bash
mini -t "在 README 增加安装步骤，并验证命令可运行"

```

## `c/--config`

指定配置来源（可以写多次，会递归合并）。默认会用：

- `mini.yaml`（内置/约定的默认配置）
- 或环境变量 `MSWEA_MINI_CONFIG_PATH` 指向的配置文件

**重点坑位（文档里也强调了）：**

> 一旦你显式写了 -c ...，就可能“不会自动使用默认配置”，所以通常要把默认配置也显式带上。
> 

**例子：在默认 mini.yaml 基础上覆盖一个温度**

```bash
mini -c mini.yaml -c model.model_kwargs.temperature=0.5 -t "……"

```

## `m/--model`

指定模型名；否则用环境变量 `MSWEA_MODEL_NAME`。

**例子**

```bash
mini -m gpt-5 -t "……"

```

## `y/--yolo`

开局进入 yolo 模式（不确认直接执行）。

## 其他你可能会用到的

- `l/--cost-limit`：成本上限（`0` 表示不限制）
- `o/--output`：输出轨迹文件（默认会保存到全局配置目录里的 `last_mini_run.traj.json`）
- `-exit-immediately`：当 agent 想结束时，不再提示你“要不要加新任务”，而是直接退出（适合脚本化/CI）

---

# 4) 三种模式怎么理解？什么时候用哪种？

mini 有三种运行模式（随时可切换）：

## ① confirm 模式（默认）

**模型提出命令 → 你确认后才执行。**

适用：

- 你不完全信任它会不会删文件、跑危险命令
- 你在熟悉项目，想“人盯着”它做

交互感觉大概是：

- 它说：我要执行 `rg "foo" -n`、`pytest -q`
- 终端问你：Execute 2 action(s)?（回车确认；输入文字就是拒绝并给理由）

## ② yolo 模式

**模型提出命令 → 立刻执行，不问你。**

适用：

- 任务很机械（比如批量格式化、简单重命名）
- 你愿意承担它乱跑命令的风险

⚠️ 通俗解释：**“自动驾驶”**。快，但出事也快。

## ③ human 模式

**你接管：你输入什么命令就执行什么命令。**

适用：

- 你想自己先探索代码库、跑调试
- 模型卡住/理解错了，你想自己推进几步再让它继续

⚠️ 注意：human 模式下，你输入的命令会被包装成“User command”记录进对话里，然后执行。

---

# 5) 运行中怎么切模式？（REPL 的“斜杠命令”）

当 mini 在等待你输入时（例如让你确认/让你补充），你可以输入：

- `/c` → 切回 confirm
- `/y` → 切到 yolo
- `/u` → 切到 human

另外：

- **Ctrl+C**：随时打断当前过程。打断后它会提示你输入一个“comment/command”，你可以顺便切模式或给指令。

还有一个非常实用的：

- `/h`：显示帮助（会告诉你当前模式、怎么切）
- `/m`：进入多行输入（适合你写一段比较长的拒绝理由或补充任务）

---

# 6) 最实用的交互例子（从启动到切模式）

## 例子 1：默认 confirm，逐步批准

```bash
mini -t "修复 tests 里一个失败用例，并确保 pytest 通过"

```

可能出现的节奏：

1. 模型：我先 `ls` 看结构、再 `pytest -q` 复现
2. 终端提示你：
    - “Execute 2 action(s)? Enter to confirm / type comment to reject…”
3. 你按回车 → 执行
4. 模型看到失败日志 → 提出修改文件 + 再跑测试
5. 每轮你都可以把关：它要执行什么，你心里有数

**你想加速**：看到它一直在跑安全命令（比如只读/测试）

你可以输入 `/y` 切 yolo，让它自己跑；等它要做危险操作再 `/c` 切回来。

---

## 例子 2：中途觉得它要干危险事 → 拒绝并说明理由

它提示要执行类似：

- `rm -rf build/` 或改动较大脚本

你不想让它直接干，就在确认提示那里输入一句拒绝理由，例如：

```
不要删除目录。请先解释为什么需要删 build/，并改用更安全的方式（例如只清理临时文件）。

```

mini 会把这当作“拒绝”并把你的理由反馈给 agent，让它调整策略。

---

## 例子 3：切到 human，你自己先探一遍，再交还给模型

你可以输入 `/u` 进入 human 模式，然后直接敲：

```bash
rg "some_function" -n
pytest -q tests/test_x.py::test_y -q

```

等你探索完了想让模型继续，你再输入 `/c` 或 `/y` 切回去即可。

---

# 7) 配置（config）到底怎么用？“递归合并”是什么意思？

mini 启动时会说：

> Building agent config from specs: [...]
> 

它会把你给的多个 `-c` 配置按顺序读入，然后**递归合并**（后面的覆盖前面的同一路径字段）。

## A. “文件 + 覆盖项”是最常见用法

```bash
mini -c mini.yaml -c agent.mode=yolo -c model.model_kwargs.temperature=0.2 -t "……"

```

## B. 只写覆盖项很容易踩坑（因为默认配置可能不会自动带上）

```bash
mini -c model.model_kwargs.temperature=0

```

这类写法在文档中被标红警告：**你可能忘了加默认 mini.yaml**，导致很多必要字段缺失/行为不符合预期。

---

# 8) 历史记录与轨迹文件：你怎么复盘上次干了啥？

mini 会：

- 保存你“上一次运行”的完整历史到全局配置目录（启动时会打印路径）
- 默认把本次运行轨迹写到类似：
    - `.../last_mini_run.traj.json`

这对排错很有用：你可以回看模型提了哪些命令、执行结果是什么。

---

# 9) 一句话给你的“选择建议”

- 你刚开始用：**confirm 模式**（默认）最稳
- 你在跑大量安全命令（grep、ls、pytest）：可以临时 `/y` 加速
- 你想自己推进：`/u` 接管，跑完关键命令再切回 `/c`

---

# 参考的实际例子

每个例子都包含：**你输入什么**、**你会看到什么**、**为什么这么设计**（通俗解释）。

---

## 例子 1：最基础——只给任务，让它自己开始（默认 confirm）

**你做：**

```bash
mini -t "把 README 里安装步骤补全，并确认命令能跑起来"

```

**你会看到（典型流程）：**

1. 它先想“了解项目”，提出执行：
- `ls`
- `cat README.md` 或 `sed -n ...`
- 可能还有 `python -m pip install -e .` 或 `make test`（取决于项目）
1. 终端问你：
- “Execute N action(s)? Enter to confirm / type comment to reject …”

**你按回车** → 执行这些命令 → 它根据输出继续下一步。

**设计解释（通俗版）：**

- confirm 就像“副驾驶模式”：它负责提出下一步，你负责点头确认，避免它误删文件或跑重命令。

---

## 例子 2：你觉得它要干危险操作——拒绝并给更安全的替代方案

**场景：**

它在 confirm 提示你要执行：

- `rm -rf build/`
- 或 `git clean -fdx`

**你在提示符里不按回车，而是输入一句话拒绝：**

```
不要删除任何目录。请先解释为什么需要清理，并改为只删除这次生成的临时文件（例如 build/tmp）。

```

**结果：**

- 这会被当成“拒绝”
- agent 会收到你的理由，然后重新规划，可能改成更保守的命令（比如只删某个文件）

**设计解释：**

- 这不是“取消任务”，而是“告诉它你不接受这一步，并给新约束”，让它继续工作但更安全。

---

## 例子 3：中途加速——从 confirm 切到 yolo，再切回来

**场景：**

它连续要跑很多“只读/无害命令”：`ls`、`rg`、`pytest -q`、`cat`…

你觉得每次都确认太慢。

**你做：**

当它在等你输入时，输入：

```
/y

```

**你会看到：**

- 输出 “Switched to yolo mode.”
- 接下来它提出的命令会**直接执行**，不再问你。

**然后你想收回控制（比如它要开始改很多文件或跑部署脚本）：**

输入：

```
/c

```

回到 confirm。

**设计解释：**

- yolo 是“自动驾驶”，confirm 是“手动确认”。随时切换是为了兼顾效率和安全。

---

## 例子 4：你接管终端——human 模式自己敲命令

**场景：**

你不想等它探索，你自己更熟项目，想先快速定位 bug。

**你做：**

在等待输入时输入：

```
/u

```

然后你直接输入命令（你敲什么就执行什么）：

```bash
rg "parse_config" -n
pytest -q tests/test_config.py::test_parse

```

**你想让 agent 继续（让它负责改代码+写测试）：**

切回：

```
/c

```

**设计解释：**

- human 模式就是“你拿键盘”。它的意义是：人类可以随时救场/加速探索，然后再把方向交回给 agent。

---

## 例子 5：Ctrl+C 打断——当它卡住或你想插话

**场景：**

它在跑一个很慢的命令或走偏了，你想立刻打断。

**你做：**

按 `Ctrl+C`

**你会看到：**

- 提示 “Interrupted… Type a comment/command (/h for available commands)”

你可以输入一个“插话”：

- 作为评论（让它改策略）：
    
    ```
    别再跑全量测试了，先只跑 tests/test_config.py
    
    ```
    
- 或者直接切模式：
    
    ```
    /u
    
    ```
    

**设计解释：**

- Ctrl+C 是“紧急刹车”，用来把你从“看它跑偏”变成“马上纠偏”。

---

## 例子 6：不写 -t，让你在启动后写多行任务（适合任务描述比较长）

**你做：**

```bash
mini

```

它问：

```
What do you want to do?

```

你输入多行任务（比如分条写清楚要求），然后按提示提交（通常 Esc 然后 Enter）：

```
1) 修复 config 解析 bug
2) 增加一个单元测试覆盖该 bug
3) 确保 pytest 全部通过

```

**设计解释：**

- 多行输入就是为了让“任务说明像 issue 一样清楚”，减少 agent 误解。

---

## 例子 7：配置叠加——“默认配置 + 覆盖项”是最推荐的姿势

### 7.1 在默认 mini.yaml 基础上，把温度调低（更稳）

```bash
mini -c mini.yaml -c model.model_kwargs.temperature=0 -t "修复一个确定性的测试失败"

```

**为什么：**

- 温度低，输出更稳定，适合“修 bug / 写测试”这种需要可复现的工作。

### 7.2 在默认 mini.yaml 基础上，开局就 yolo

```bash
mini -c mini.yaml -c agent.mode=yolo -t "批量把 imports 排序并运行格式化工具"

```

**为什么：**

- 这种任务机械、风险相对低，yolo 可以省很多确认时间。

### 7.3 “只写覆盖项”的坑（演示）

```bash
mini -c model.model_kwargs.temperature=0 -t "……"

```

**可能出问题：**

- 因为你没把默认配置带上，很多必要字段可能缺失或行为不符合你预期。

**通俗解释：**

- 这像你只写“我要把方向盘灵敏度调成 0”，但你没装发动机配置，车可能根本启动不了。

---

## 例子 8：指定模型（-m）——快速试不同模型表现

```bash
mini -m gpt-5 -t "解释并修复这个函数的边界条件"

```

**设计解释：**

- 模型不同，策略和代码质量可能不同。`m` 让你不用改配置文件就能切换。

---

## 例子 9：输出轨迹（-o）——方便复盘“它到底做了什么”

```bash
mini -t "修复 bug 并加测试" -o /tmp/run1.traj.json

```

**你会得到：**

- 一个轨迹文件，记录了对话、每次提议的命令、执行输出等。

**设计解释：**

- 轨迹就像“飞行记录仪”：方便你审计、复现、对比两次运行差异。

---

## 例子 10：`-exit-immediately`——适合脚本化/批处理

```bash
mini --exit-immediately -t "只跑测试并报告结果"

```

**效果：**

- 当 agent 认为任务完成时，它不会再问你“要不要加新任务”，而是直接退出。

**设计解释：**

- 这个选项让 mini 更像一个“可自动化工具”，方便接进 CI 或脚本流程。

---

# Source Code 简单分析

---

## 0) 先看整体：从 `mini` 命令到“执行命令”的链路

你敲：

```bash
mini -t "..." -c ... -y ...

```

核心链路是：

1. **Typer CLI** 解析命令行参数 → 得到 `model_name/task/yolo/cost_limit/config_spec/output/...`
2. `get_config_from_spec()` 把 `c` 的“文件/键值对配置”解析成 dict
3. `recursive_merge()` 递归合并多个配置（后者覆盖前者）
4. 构造：
    - `model = get_model(config["model"])`
    - `env = LocalEnvironment(**config["environment"])`
    - `agent = InteractiveAgent(model, env, **config["agent"])`
5. `agent.run(task)` 开始循环：
    - **向模型 query**（或 human 模式下你输入命令）
    - 产生 `actions`（要执行的命令）
    - `execute_actions()` 在 confirm/yolo/human 规则下执行
    - 打印对话、保存轨迹

---

## 1) `mini` 启动脚本（Run script）详细分析 + 注释版关键代码

你给的脚本本质是一个 CLI 包装器：**收集配置 → 创建 agent+env+model → 运行**。

### 1.1 关键点：默认配置文件怎么决定？

源码：

```python
DEFAULT_CONFIG_FILE = Path(os.getenv("MSWEA_MINI_CONFIG_PATH", builtin_config_dir / "mini.yaml"))
DEFAULT_OUTPUT_FILE = global_config_dir / "last_mini_run.traj.json"

```

含义：

- 如果你设置了环境变量 `MSWEA_MINI_CONFIG_PATH`，就用它指向的配置文件
- 否则用内置目录 `builtin_config_dir/mini.yaml`
- 输出轨迹默认写到全局配置目录下的 `last_mini_run.traj.json`

> 设计意图：你可以通过环境变量“全局指定”默认 config；否则就用项目自带的默认配置。
> 

---

### 1.2 `c/--config` 的“坑位”为什么存在？

你贴的 help 文案有一句很重要：

> If you set this option, the default config file will not be used.
> 

在 Typer 里，**Option 的默认值只有在用户没有提供该参数时才会使用**。

也就是说：`config_spec` 的默认是 `[DEFAULT_CONFIG_FILE]`，但你只要自己传了 `-c`，默认就不生效了。

这就是为什么文档强调：如果你要覆盖某个字段，通常要这样写：

```bash
mini -c mini.yaml -c model.model_kwargs.temperature=0.5 ...

```

而不是只写：

```bash
mini -c model.model_kwargs.temperature=0.5 ...

```

---

### 1.3 “配置合并”的实现：递归 merge + UNSET 哨兵

下面是启动脚本最关键的一段（我加了注释）：

```python
configs = [get_config_from_spec(spec) for spec in config_spec]

configs.append({
    "agent": {
        # 如果命令行传了 -y，则设置 agent.mode=yolo
        # 否则用 UNSET 表示“别覆盖配置文件里的 mode”
        "mode": "yolo" if yolo else UNSET,

        # 命令行传 -l/--cost-limit 就覆盖，否则不覆盖
        "cost_limit": cost_limit or UNSET,

        # --exit-immediately -> confirm_exit=False（不再提示“要不要加新任务”）
        "confirm_exit": False if exit_immediately else UNSET,

        # output_path 覆盖默认轨迹输出位置
        "output_path": output or UNSET,
    },
    "model": {
        "model_class": model_class or UNSET,
        "model_name": model_name or UNSET,
    },
})

config = recursive_merge(*configs)

```

### 设计解释（很关键）

- `UNSET` 是一个“哨兵值”，表示：**这次不想覆盖**。
    
    这样命令行只覆盖你显式传入的字段，其他字段仍来自配置文件。
    
- `recursive_merge()` 让你能写 `c model.model_kwargs.temperature=...` 这种“深层字段覆盖”，而不是整块替换。

### ⚠️ 这里有个非常值得注意的细节/潜在 bug

这一行：

```python
"cost_limit": cost_limit or UNSET,

```

如果你真的按 help 所说“设为 0 禁用”，即：

```bash
mini -l 0 ...

```

那么 `cost_limit` 在 Python 里是 `0.0`，属于 falsy，结果会变成 `UNSET`，也就是**不会覆盖配置**。

=> 这会导致“0 禁用”不一定生效（会继续沿用配置文件里的 cost_limit）。

更稳的写法应该类似：

```python
"cost_limit": UNSET if cost_limit is None else cost_limit

```

同理，其他用 `x or UNSET` 的地方，只要 `x` 可能合法为 `0 / "" / False`，就会有同类问题。

---

### 1.4 任务输入：为什么要多行输入？

```python
if not task:
    console.print("[bold yellow]What do you want to do?")
    task = _multiline_prompt()

```

因为任务描述通常像 issue 一样可能很长、要分条写（尤其“约束/验收标准”），多行输入能降低模型误解。

---

### 1.5 构建并运行 agent

```python
model = get_model(config=config.get("model", {}))
env = LocalEnvironment(**config.get("environment", {}))
agent = InteractiveAgent(model, env, **config.get("agent", {}))
agent.run(task)

```

设计意图：

- `model` 与 `env` 完全由 config 驱动，方便替换模型/环境
- `InteractiveAgent` 把“确认/接管”等交互能力加在默认 Agent 上

---

## 2) `InteractiveAgent`（Agent class）详细分析 + 注释版关键代码

这一段是真正实现“confirm/yolo/human + slash commands + Ctrl+C 打断 + 轨迹/历史”的核心。

---

### 2.1 配置类：三种模式 + 白名单 + 是否确认退出

```python
class InteractiveAgentConfig(AgentConfig):
    mode: Literal["human", "confirm", "yolo"] = "confirm"
    whitelist_actions: list[str] = []
    confirm_exit: bool = True

```

含义：

- `mode` 默认 confirm
- `whitelist_actions`：在 confirm 模式下，**匹配正则的命令永远不需要确认**（比如 `ls`, `rg`, `cat`）
- `confirm_exit`：模型说“我要结束”时，是否要再问你能不能加新任务（脚本里 `-exit-immediately` 会把它设为 False）

---

### 2.2 历史记录：为什么用 prompt_toolkit 的 FileHistory？

```python
_history = FileHistory(global_config_dir / "interactive_history.txt")
_prompt_session = PromptSession(history=_history)
_multiline_prompt_session = PromptSession(history=_history, multiline=True)

```

通俗解释：

- 这让你的输入像 shell 一样可回溯（上下箭头翻历史、Ctrl+R 搜索）
- 多行输入也共享同一个历史文件

---

### 2.3 add_messages：为什么要重写？

它不仅把消息存进上下文，还负责把每一步打印得“像一个交互式工具”。

关键点：

```python
if role == "assistant":
    console.print(f"mini-swe-agent (step {self.n_calls}, ${self.cost:.2f}):")
else:
    console.print(f"{role.capitalize()}:")
console.print(content, markup=False)

```

设计意图：

- 让你随时看到当前 step、花了多少钱（成本/调用次数）
- `markup=False` 避免内容被 Rich 当成富文本误解析

---

### 2.4 query：human 模式为什么“你输入命令会变成一个 user message + action”？

这是很核心的设计：human 模式下你敲的命令，仍以统一的数据结构进入 agent 流程（message.extra.actions）。

```python
if self.config.mode == "human":
    command = self._prompt_and_handle_slash_commands("> ")
    match command:
        case "/y" | "/c":
            pass  # 切模式后继续走 LM query
        case _:
            msg = {
                "role": "user",
                "content": f"User command: \n```bash\n{command}\n```",
                "extra": {"actions": [{"command": command}]},
            }
            self.add_messages(msg)
            return msg
# 不是 human 或 human 下输入了 /y /c，就让 LM 来回复
return super().query()

```

**设计亮点：**

- human 下你输入的命令被包装成标准“action”，因此执行路径与 LM 生成 action 是一致的
- 你在 human 模式输入 `/y` 或 `/c` 会切模式，然后继续交给 LM（这很符合直觉）

---

### 2.5 Ctrl+C 打断：为什么要抛 UserInterruption？

```python
except KeyboardInterrupt:
    interruption_message = self._prompt_and_handle_slash_commands("Interrupted... > ").strip()
    if not interruption_message or interruption_message in self._MODE_COMMANDS_MAPPING:
        interruption_message = "Temporary interruption caught."
    raise UserInterruption({...})

```

设计意图：

- Ctrl+C 不只是“停止”，而是“给你插话/纠偏机会”
- 用异常 `UserInterruption` 把你这句插话注入对话，让 agent 重新规划（而不是直接退出程序）

---

### 2.6 execute_actions：confirm 的核心入口（并且保证“部分输出也能保存/显示”）

这段非常关键：它负责执行命令，并在拒绝/中断/提交时仍然写 observation。

```python
actions = message.get("extra", {}).get("actions", [])
commands = [action["command"] for action in actions]
outputs = []
try:
    self._ask_confirmation_or_interrupt(commands)  # confirm 模式下会询问
    for action in actions:
        outputs.append(self.env.execute(action))    # 真正执行
except Submitted as e:
    self._check_for_new_task_or_submit(e)           # 处理“要结束/要加新任务”
finally:
    result = self.add_messages(
        *self.model.format_observation_messages(message, outputs, self.get_template_vars())
    )
return result

```

**设计解释：**

- `try/finally` 的目的是：哪怕中途异常（比如执行到一半你拒绝了、或者 Submitted），也能把“目前已经执行的输出”格式化成 observation，写进轨迹/上下文，方便复盘。

---

### 2.7 confirm 细节：白名单 & 正则匹配方式

```python
def _should_ask_confirmation(self, action: str) -> bool:
    return self.config.mode == "confirm" and not any(re.match(r, action) for r in self.config.whitelist_actions)

```

注意它用的是 `re.match`（从字符串开头匹配）。所以白名单通常应该写成：

- `^ls\b`
- `^rg\b`
- `^cat\b`
- `^pytest\b`

否则如果你写 `ls`，`re.match("ls", "cls")` 不会匹配，但 `re.match("ls", "ls -la")` 会；

建议用 `^` 显式锚定，避免意外匹配。

---

### 2.8 “确认提示”里输入 `/y` 是一个小巧但很实用的设计

确认逻辑：

```python
match user_input := self._prompt_and_handle_slash_commands(prompt).strip():
    case "" | "/y":
        pass  # confirmed
    case "/u":
        raise UserInterruption(... "Switching to human mode")
    case _:
        raise UserInterruption(... "User rejected with message")

```

因为 `_prompt_and_handle_slash_commands` 看到 `/y` 会**切到 yolo**并返回 `/y`，而这里又把 `/y` 当做“确认”。

=> 所以你可以在某一步确认时输入 `/y`，它会同时做到两件事：

1. 立刻确认这次要执行的命令
2. 从此切换到 yolo（后面不再问）

这个交互很丝滑：相当于“这一步我同意，并且接下来你都别问了”。

---

### 2.9 结束时能加新任务：confirm_exit 的作用

```python
if self.config.confirm_exit:
    message = "Agent wants to finish. Type new task or Esc then enter to quit."
    if new_task := self._prompt_and_handle_slash_commands(..., _multiline=True).strip():
        raise UserInterruption({"content": f"The user added a new task: {new_task}"})
raise e

```

设计意图：

- agent 达成目标后，你可能会想“顺手再做一件事”（比如“再跑一下 lint”“再改下文档”）
- confirm_exit=True 时，给你一个“追加任务窗口”
- `-exit-immediately` 会关掉这个窗口，适合脚本/自动化

---

## 3) 你给的源码中，“设计”最核心想解决的 4 件事

1. **安全性 vs 效率**
    - confirm 保守、安全
    - yolo 快速、自动
    - human 让你随时接管
2. **交互不中断工作流**
    - Ctrl+C 不是退出，而是“插话/纠偏”
    - 拒绝不是终止，而是把拒绝理由注入对话，让 agent 改方案
3. **统一的数据结构**
    - 不管是 LM 生成命令还是你手动敲命令，都以 `message.extra.actions` 进入执行路径
    - 执行结果统一走 `format_observation_messages`
4. **可复盘/可审计**
    - history 文件 + trajectory 输出
    - add_messages 把每一步清晰打印

---