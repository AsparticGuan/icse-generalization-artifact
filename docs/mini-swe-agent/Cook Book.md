# Cook Book

# Cookbook 是干什么的

**当 “改 `mini.yaml`/配置” 不够用时，就把 mini-SWE-agent 当成一套可拼装的积木**，用几百行以内的 Python 代码做出“你自己的 mini”。

官方把配方总结成 4 个可替换组件：**Agent（控制流）/ Environment（执行动作）/ Model（调用大模型）/ Run script（把它们绑起来并提供入口）**。 ([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/cookbook/))

---

# 4 个组件各管什么

## 1) Agent：决定控制流（循环、何时停、怎么把消息串起来）

### `DefaultAgent` 的实现非常直白：

- `run()` 初始化 system+user 两条消息，然后 `while True: step()`；`step()` = `query()`（问模型）+ `execute_actions()`（跑动作）；
- 最后如果最新消息 `role == "exit"` 就结束。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/agents/default/))

你会在 Cookbook 里看到的“Subclassing the agent”，本质就是**改写 `query()` 或 `execute_actions()` 来插钩子/改规则**。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/cookbook/))

## 2) Environment：决定动作怎么执行（本地 / docker / 沙箱 / 云）

Environment 的职责是“执行 agent/模型产出的 action”。

例如 `LocalEnvironment.execute()` 直接 `subprocess.run(..., shell=True)`，并且有一个很关键的小机制：

如果命令输出第一行是 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 且返回码为 0，就抛出 `Submitted` 异常让 agent 退出。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/environments/local/))

Cookbook 里提到的“Subclassing the environment”，就是**改写 `execute()`：**

**比如把某些动作转给 Python 函数、或者在执行前做校验/隔离**。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/cookbook/))

## 3) Model：决定怎么问大模型、怎么把输出解析成 actions

### 以 `LitellmModel` 为例：

- 它会调用 `litellm.completion(..., tools=[BASH_TOOL])`，然后把模型返回的 tool calls 解析成 `message["extra"]["actions"]`，再由 agent 去执行这些 actions；
- 另外它也负责 cost tracking 等元信息。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/models/litellm/))

### `get_model()` 是一个“方便函数”：

- 从参数 / config / 环境变量里推断 model_name，并选择 model_class；
- 对 Anthropic 模型还会默认加 cache control；还有全局 cost/call limit。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/models/utils/))

## 4) Run script：把三件套组装起来，并提供 CLI/入口

- `hello_world.py` 是最小模板：`DefaultAgent(LitellmModel, LocalEnvironment, **default.yaml里的agent配置)` 然后 `agent.run(task)`。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/run/hello_world/))
- `mini.py` 是“完整版入口”：用 Typer 做 CLI，读取并递归 merge 多个 config spec，最后 `get_model()`、`get_environment()`、`get_agent()` 组装后运行。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/run/mini/))

Cookbook 鼓励你：**直接复制 hello_world 或 mini 作为起点，改出自己的 run script**。([Mini Swe Agent](https://mini-swe-agent.com/latest/advanced/cookbook/))

---

# 用一组“可跑起来的微场景”理解 Cookbook 的设计

下面每个例子都尽量小、实际，并且对应我上面讲的“Agent / Environment / Model / Run script”四块积木，以及常见的扩展点（拦截 actions、退出、校验、换入口）。

---

## 例子 1：最小“拼装”——三件套一行就能换（Agent/Env/Model 解耦）

**目标：** 同一个 Agent，不改任何逻辑，只换模型或执行环境，就能跑起来。

```python
from minisweagent.agents.default import DefaultAgent
from minisweagent.models import get_model
from minisweagent.environments.local import LocalEnvironment

task = "列出当前目录的文件，然后打开 a.py 读前 30 行。"

agent = DefaultAgent(
    get_model(input_model_name="anthropic/claude-sonnet-4-5-20250929"),
    LocalEnvironment(),
)
agent.run(task)
```

**你会看到的“设计点”：**

- `DefaultAgent` 只负责控制流：问模型 → 取 actions → 执行 → 把观察结果喂回模型 → 循环。
- `LocalEnvironment` 只负责把 action 里的 command 真正跑掉（subprocess）。
- `get_model()` 只负责挑选/构建合适的 model 实例（比如 LitellmModel 及其默认行为）。

**通俗理解：**

Agent 是“导演”、Environment 是“剧组执行”、Model 是“编剧”。你换演员/换片场不需要改导演的剧本结构。

---

## 例子 2：看懂 action 是怎么从模型输出进入执行器的（messages/extra/actions 数据形状）

**目标：** 直观看到“模型输出 actions → agent 读 message['extra']['actions'] → env 执行”。

一个典型 message 形状大概像这样（伪结构）：

```json
{
  "role": "assistant",
  "content": "我先列一下目录。",
  "extra": {
    "actions": [
      { "tool": "bash", "command": "ls" }
    ]
  }
}
```

**执行链路：**

1. `agent.query()` 从模型拿到一条 assistant message（里面带 `extra.actions`）。
2. `agent.execute_actions(message)` 遍历 `message["extra"]["actions"]`。
3. 对每条 action：`environment.execute(action, cwd=...)` → 返回一个 observation dict（例如 `{"output": "...", "return_code": 0}`）。
4. agent 把 observation 通过 `model.format_observation_messages(...)` 变成“tool/observation 消息”再追加回 messages，继续下一轮。

**通俗理解：**

- 模型不是直接“跑命令”，它只是生成“待执行计划（actions）”；
- 执行器跑完把结果“回填”为 observation，再给模型做下一步决策。

---

## 例子 3：把一部分命令“改走 Python 函数”——拦截 Agent.execute_actions

**目标：** 你希望有些动作不走 shell（更快、更安全、更可控），例如 `python_function add 1 2`。

```python
from minisweagent.agents.default import DefaultAgent
import shlex

def python_function(*args) -> dict:
    # 超简单例子：只做加法
    if args and args[0] == "add":
        a, b = int(args[1]), int(args[2])
        return {"output": str(a + b)}
    return {"output": f"unknown args={args}"}

class AgentWithPythonFunctions(DefaultAgent):
    def execute_actions(self, message: dict) -> list[dict]:
        for action in message.get("extra", {}).get("actions", []):
            command = action.get("command", "")
            if command.startswith("python_function"):
                args = shlex.split(command.removeprefix("python_function").strip())
                obs = python_function(*args)

                # 关键点：把 python_function 的返回值“伪装成”一次工具执行观察结果
                return self.add_messages(
                    self.model.format_observation_messages(
                        message, [obs], self.get_template_vars()
                    )
                )

        return super().execute_actions(message)
```

**运行时会发生什么：**

- 模型产出 action：`python_function add 1 2`
- 你的 `execute_actions()` 抢先识别到它 → 不调用 environment → 直接调用 Python 函数得到 `{"output": "3"}`
- 然后通过 `format_observation_messages` 把它变成模型可理解的“观察结果”，继续 loop

**为什么这设计很“好用”：**

- 你不用改 Model，也不用改 Environment；只是“在 Agent 执行动作前加一个路由规则”。

---

## 例子 4：让 agent 在某个动作出现时立刻退出——抛 Submitted

**目标：** 

你希望当模型发出 `submit` 命令时立刻停止（比如你做自动评测，希望明确终止信号）。

**更具体点：**

在 mini 里，模型每一步会产出一个 message，其中可能带 `extra.actions`；这些 actions 会被 `execute_actions()` 逐个执行。

你想要的就是：**一旦 actions 里出现“提交/收工”信号，就不再继续后续 step（不再问模型、不再执行更多命令），立刻结束整次 run**。

```python
from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import Submitted

class AgentQuitsOnSubmit(DefaultAgent):
    def execute_actions(self, message: dict) -> list[dict]:
        for action in message.get("extra", {}).get("actions", []):
            if action.get("command", "") == "submit":
                raise Submitted({
                    "role": "exit",
                    "content": "The agent has finished its task.",
                    "extra": {"exit_status": "Submitted", "submission": ""},
                })
        return super().execute_actions(message)
```

**结合这段代码逐行看发生了什么：**

- `message: dict`：就是模型刚产出的那条 assistant message（通常结构里会有 `message["extra"]["actions"]`）。
- `for action in message.get("extra", {}).get("actions", [])`：把这一步模型想执行的每个 action（比如 bash 命令）拿出来看。
- `if action.get("command", "") == "submit"`：只要看到 command 恰好是 `submit`，就认为“模型已经声明任务完成并提交”。
- `raise Submitted({...})`：**直接抛异常**，并携带一条“退出消息”字典（`role: "exit"` + 一些元信息）。
    - 这里的 `extra.exit_status = "Submitted"` 是给轨迹/日志用的：让你事后能明确知道“是因为提交而结束”，而不是报错/超时。
    - `submission` 字段可以承载最终答案（这个例子填空字符串，是因为演示只关心“退出机制”）。

**你会看到的“设计点”：**

- 用异常 `Submitted` 做“控制流跳出”，而不是用一堆 if/flag 让 run loop 变复杂。
更直观一点：`execute_actions()` 本来是“执行动作并返回观察结果”的地方，但“提交”不是一个普通动作——它代表 **终止整个 agent 控制流**。用异常能立刻从多层调用栈里跳出来，不需要在每一层都写“如果提交就 return/break”的分支。
- `DefaultAgent.run()` 会捕获这类异常，把退出消息纳入轨迹，然后结束。
具体含义是：你的 `raise Submitted({...})` 不会让程序“崩掉”，而是被框架当作“正常结束”的一种方式处理：把 `role="exit"` 的消息追加到轨迹里，然后 `run()` 返回/打印最终输出。

**顺带一个更“实际”的常见用法：**

- 有的环境（例如本地环境）还支持一种“隐式提交协议”：命令输出第一行是某个哨兵字符串且 return_code=0，就当作提交并退出。
    
    这让“退出信号”可以从 **prompt 层/命令输出层** 来触发，不一定非要显式 `submit` action。
    更具体点：你可以不让模型生成 `submit` 这个 action，而是让模型执行一个普通命令（比如 `bash -lc "..."`），并要求该命令在任务完成时输出一行固定的“收工标记”（哨兵字符串）。环境在拿到执行结果后，检查到“第一行是哨兵 + return_code=0”，就会触发同样的 `Submitted` 结束流程。这样做的好处是：
    
    - 终止条件可以绑定在“执行结果”上（更贴近评测/基准的 submit 约定）；
    - 你不必依赖模型一定会生成 `submit` 这种特定 action（减少格式偏差带来的不确定性）。

---

## 例子 5：动作安全校验——执行前挡掉危险命令，并让模型“重试”

**目标：** 

你想禁止明显危险的命令（例如 `rm -rf /`、`mkfs.*`），但又希望 agent 不要直接崩掉，而是告诉模型“被挡了，换一种方案”。

```python
import re
from minisweagent.agents.default import DefaultAgent, AgentConfig
from minisweagent.exceptions import FormatError

class ValidatingAgentConfig(AgentConfig):
    forbidden_patterns: list[str] = [r"rm -rf /", r"mkfs\.", r"sudo.*passwd"]

class ValidatingAgent(DefaultAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, config_class=ValidatingAgentConfig)

    def execute_actions(self, message: dict) -> list[dict]:
        for action in message.get("extra", {}).get("actions", []):
            cmd = action.get("command", "")
            for pat in self.config.forbidden_patterns:
                if re.search(pat, cmd, re.IGNORECASE):
                    # 关键：抛 FormatError，会被 agent 捕获并当成“反馈消息”喂回模型继续跑
                    raise FormatError(self.model.format_message(
                        role="user",
                        content=f"Action blocked by policy: {pat}",
                    ))
        return super().execute_actions(message)
```

**实际效果（很常见）：**

- 模型想跑：`rm -rf /tmp/foo`（假设你的规则更严格）
- agent 拦住 → 抛 `FormatError` → 这条“被挡提示”变成新的 user message
- 模型下一步通常会改为更安全的命令（例如只删除特定路径、或先确认路径存在）

**设计点：**

- 规则放在 `AgentConfig`：你能通过 config 调整，而不用改代码。
- 用 `FormatError` 把“执行失败/不允许”反馈给模型，而不是 silent fail 或直接退出。

---

## 例子 6：用 MSWEA_DEFAULT_RUN 把你的自定义入口变成“默认 mini”

**目标：** 

你写了一个项目专用的 run script（例如默认用 Docker + ValidatingAgent），希望输入 `mini ...` 就自动走它，而不是官方默认入口。

假设你有文件 `run_mymini.py`：

```python
# run_mymini.py
from minisweagent.agents.default import DefaultAgent
from minisweagent.models import get_model
from minisweagent.environments.docker import DockerEnvironment

def main(task: str):
    agent = DefaultAgent(
        get_model(input_model_name="gpt-4o"),
        DockerEnvironment(),
    )
    agent.run(task)
```

在项目的 `.env` 或 direnv 里设：

```bash
export MSWEA_DEFAULT_RUN="your_package.run_mymini:main"
```

**你会得到的体验：**

- 进入这个项目目录，`mini "修复 bug"` 走你自己的 main；
- 换到另一个项目目录，走另一个入口（非常适合多项目不同环境/不同 guardrail）。

**设计点：**

- “入口可替换”把扩展成本降到很低：你不用 fork 整个 CLI，只换默认 run target。

---

# 一句话把这些例子串起来（你应该形成的直觉）

- **想改流程/规则 → 子类化 Agent（通常改 `execute_actions()` 最多）**
- **想改执行后端/隔离策略 → 子类化 Environment（改 `execute()`）**
- **想换模型供应商/工具格式 → 换 Model 或用 `get_model()`**
- **想把这一套变成项目默认 → 写 run script + `MSWEA_DEFAULT_RUN`**