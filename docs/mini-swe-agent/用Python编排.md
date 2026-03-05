# 用Python编排

# Python bindings（把 mini-SWE-agent 当作 Python 库来用）

官方页面的“Hello world”其实已经把 **Python bindings 的最小闭环**展示出来了：

你只需要准备 **Model + Environment + Agent**，然后 `agent.run(task)`。 ([Mini Swe Agent](https://mini-swe-agent.com/v2/usage/python_bindings/))

## 1) 最小可运行范式：Model + Environment + Agent + run

```python
import logging
from minisweagent.agents.default import DefaultAgent
from minisweagent.models import get_model
from minisweagent.environments.local import LocalEnvironment

logging.basicConfig(level=logging.DEBUG)

agent = DefaultAgent(
    get_model(input_model_name="anthropic/claude-sonnet-4-5-20250929"),
    LocalEnvironment(),
)

result = agent.run("Write a hello world program")
print(result)  # dict: exit_status / submission 等
```

- `get_model(...)`：把“模型名/配置/环境变量”等输入解析成一个可用的 `Model` 实例。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/models/utils/))
- `LocalEnvironment()`：负责真正执行 action（例如 shell 命令），本地直接 `subprocess.run`，不做隔离。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/environments/))
- `DefaultAgent(...)`：负责“把历史 messages 喂给模型 → 拿到 actions → 交给 env 执行 → 把执行结果再喂回模型”。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))
- `agent.run(task)`：会循环调用 `step()` 直到出现 `role="exit"`，并返回最后一条消息的 `extra`（其中含 `exit_status/submission`）。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))

> 通俗理解：**模型负责“想下一步做什么”，环境负责“真的去做”，Agent 负责“把两者接起来并记流水账（trajectory/messages）”。**
> 

---

## 2) get_model 怎么“选模型/选实现类”的（常见坑点）

`get_model` 的行为大致是：

1. 先决定 `model_name` 从哪来：优先用 `input_model_name`；否则看 `config["model_name"]`；否则读环境变量 `MSWEA_MODEL_NAME`；都没有就报错提示去跑 `mini-extra config setup`。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/models/utils/))
2. 再决定用哪个 `Model` 类来实例化：如果显式传了 `model_class`（shortcut 或完整 import path），就用它；否则默认回落到 `LitellmModel`。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/models/utils/))
3. 一个小细节：如果模型名看起来像 Anthropic（包含 anthropic/sonnet/opus/claude 等），且你没设置 `set_cache_control`，会默认帮你设成 `"default_end"`。([Mini Swe Agent](https://mini-swe-agent.com/latest/reference/models/utils/))

---

## 3) “跑一次就留痕”：保存 trajectory（output_path）

`DefaultAgent.run()` 每一步都会在 `finally` 里调用 `self.save(self.config.output_path)`

**因此只要你设置 `output_path`，就会持续落盘保存轨迹。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))**

```python
from pathlib import Path
agent = DefaultAgent(
    get_model(input_model_name="anthropic/claude-sonnet-4-5-20250929"),
    LocalEnvironment(),
    output_path=Path("runs/demo.traj.json"),
)
agent.run("Write a hello world program")
```

---

## 4) 换执行后端：本地 vs Docker（以及其它隔离方案）

环境类决定“action 到底在哪跑”：

- `local`：宿主机直接执行（最快、最方便、**无隔离**）。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/environments/))
- `docker`：通过 `docker exec` 在容器里执行（更安全，适合评测/跑 SWE-bench）。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/environments/))
- 还有 `singularity / swerex_* / bubblewrap` 等。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/environments/))

---

## 5) 人在回路：InteractiveAgent（最直接的“人工审批/干预”）

cookbook 里给了直接替换 agent 类的例子：

把 `DefaultAgent` 换成 `InteractiveAgent`，其余照旧。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/cookbook/))

---

# Agent control flow（默认 Agent 的控制流怎么跑、怎么改）

官方文档把默认控制流拆成了 4 个核心方法：`run → step → query → execute_actions`，并给了对应实现代码。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))

## 1) 一步 step() 到底做什么？

```python
def step(self):
    return self.execute_actions(self.query())
```

也就是两段：

1. `query()`：把当前 `self.messages` 发给模型，模型回一条 message（可能带 `extra.actions`），并累计 cost / calls。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))
2. `execute_actions(message)`：逐个把 `extra.actions` 交给 `env.execute(action)` 跑，把 outputs 再交给 `model.format_observation_messages(...)` 变成“`observation messages`”，最后统一 `add_messages(...)` 记进轨迹。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))

> 通俗理解：**query 产出“计划/命令”，execute_actions 产出“执行结果/观察”，两者交替推进。**
> 

---

## 2) run() 为什么能“自动停下来”？靠 role="exit" + 异常控制流

`run()` 会：

- 先初始化两条消息：`system` + `user(instance_template)`。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))
- 然后 `while True` 循环 step：
    - 如果抛出 `InterruptAgentFlow`（一种“可控异常”），就把异常里携带的 messages 加进轨迹，然后继续循环。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))
    - 如果是其它异常：记录并 re-raise。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))
    - 每次都会 `finally: save(output_path)`。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))
- 一旦 `messages[-1].role == "exit"`，break，返回最后 `extra`。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))

---

## 3) “结束任务”的标准方式：Environment 发现完成标记后抛 Submitted

文档给了一个关键机制：

环境在执行输出里检测到魔法字符串（示例：第一行是 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`）就抛出 `Submitted(...)`，最终形成 `role="exit"` 消息，让 `run()` 退出。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))

> 这解释了一个常见现象：**不是模型自己“return/exit”，而是环境通过“输出约定”来判定完成。**
> 

---

## 4) 限制与错误怎么进轨迹：LimitsExceeded / FormatError / Timeout / UserInterruption…

- `query()` 在每次调用模型前检查 step/cost 限制，超了就抛 `LimitsExceeded`，并构造一条 `role="exit"` 消息。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))
- 其它如 `FormatError/TimeoutError/UserInterruption` 等也走 `InterruptAgentFlow` 这条“异常携带 messages”的通道，因此 **错误信息会被写进 trajectory**，控制流也更容易扩展。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/control_flow/))

---

# 一组“能直接对上 DefaultAgent 代码”的例子

每个例子我都用同一种方式讲清楚：

- `run()` 里 `messages` 一开始长什么样
- 每次循环 `step()` 里到底发生了哪些调用：`query → execute_actions`
- `messages` 如何被追加（以及何时追加不了）
- 什么情况下会出现 `role="exit"` 让循环停止

---

# 例子 1：最普通的一步（模型说“ls”，环境执行，观测回灌）

## 场景

任务：`"列出当前目录文件"`

## run() 初始化 messages（固定两条）

```json
[
  {"role":"system","content":"...system_template 渲染结果..."},
  {"role":"user","content":"...instance_template 渲染结果（含 task）..."}
]
```

## 第 1 次 step() 的真实调用顺序

### 1) query()

模型收到 `messages`，返回一个 assistant message，带 action：

```json
{
	"role":"assistant",
	"content":"我先列一下目录。",
	"extra":{"actions":[{"tool":"bash","command":"ls"}], "cost":0.01}
}
```

`Agent.query()` 会：

- `n_calls += 1`
- `self.cost += cost`
- `add_messages(assistant_message)` 追加进去

此时 `messages` 变成（新增了第 3 条）：

```json
[system, user, assistant(action=ls)]
```

### 2) execute_actions(message)

对 `extra.actions` 逐个调用环境：

- `env.execute({"tool":"bash","command":"ls"})` → 返回 output（比如）

```json
{"output":"a.py\nb.py\n", "exit_code":0}
```

然后 `model.format_observation_messages(...)` 把 outputs 包装成“观测消息”（通常是 `role="tool"` 或类似结构），例如：

```json
[
  {"role":"tool","name":"bash","content":"a.py\nb.py\n"}
]
```

`add_messages(tool_observation)` 追加进去。

## 这一步结束后，messages 的样子

```json
[system, user, assistant(ls), tool(ls_output)]
```

> 你可以把它理解成：**assistant 产生命令 → tool 产生命令结果 → 下一轮 assistant 再基于结果继续想。**
> 

---

# 例子 2：两步循环（第一次读文件，第二次跑测试）

任务：`"修复 a.py 并跑测试"`

## 第 1 步：打开文件

- `query()` 得到：`cat a.py`
- `execute_actions()` 得到：文件内容观测

messages 片段长这样：

```json
{"role":"assistant","content":"我先看 a.py。",
"extra":{"actions":[{"tool":"bash","command":"cat a.py"}]}}
{"role":"tool","name":"bash","content":"def add(a,b):\n  return a-b\n"}
```

## 第 2 步：跑测试

- `query()` 得到：`pytest -q`
- `execute_actions()` 得到：测试失败输出观测

messages 片段：

```json
{"role":"assistant","content":"跑一下测试定位问题。","extra":{"actions":[{"tool":"bash","command":"pytest -q"}]}}
{"role":"tool","name":"bash","content":"FAILED test_add ... expected 3 got -1\n"}
```

> 这就是你看到的控制流图里：`Agent.step()` 每次都做一套 “问模型 → 执行动作 → 把观测加回 messages”。
> 

---

# 例子 3：如何“自动停下来”（Submitted / role="exit"）

这里是最容易迷糊的点：

**run() 并不是靠模型“return”结束，而是靠 messages 里出现 `role="exit"`。**

## 场景 A：环境通过“魔法字符串”判定完成（Submitted）

假设模型最后一步让环境执行一个命令，并且命令输出第一行是：

```
COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
<后面是最终提交文本...>
```

Environment在 `execute()` 里检查到这个开头，就会 `raise Submitted({...exit message...})`。

### 关键后果：execute_actions 里“观测消息”不会正常追加

因为异常直接中断了这一行：

```python
outputs = [self.env.execute(action) for action in actions]
```

`run()` 捕获到 `InterruptAgentFlow`（Submitted 继承它），然后：

```python
self.add_messages(*e.messages)
```

通常 `e.messages` 里就含一条 exit：

```json
{
	"role":"exit",
	"content":"...",
	"extra":{"exit_status":"Submitted","submission":"<最终答案>"}
}
```

### run() 下一行判断退出

```python
if self.messages[-1].get("role") == "exit":
    break
```

所以循环结束。

---

# 例子 4：触发 step_limit / cost_limit（LimitsExceeded）

## 场景

你设置：`step_limit=2`，但任务至少需要 3 次模型调用。

`query()` 一开始就会检查：

```python
if 0 < step_limit <= n_calls:
    raise LimitsExceeded({"role":"exit", ...})
```

于是第三次准备 query 时直接抛异常，run() 捕获并加一条 exit：

```json
{"role":"exit",
 "content":"LimitsExceeded",
 "extra":{"exit_status":"LimitsExceeded","submission":""}}
```

然后下一轮检查 `role=="exit"`，立刻停止。

> 这个例子告诉你：**限制是在 query 前检查的**，也就是说“还没问模型”就可能结束。
> 

---

# 例子 5：模型格式不对（FormatError）会怎么进轨迹

假设你的 Model 期望 assistant message 的 `extra.actions` 是一个 list，但模型回了个乱格式（例如把 actions 写成字符串）：

```json
{"role":"assistant","content":"我执行一下。","extra":{"actions":"ls"}}
```

Model 或解析层发现格式不符合规范，会 `raise FormatError(messages=[...])`（它也继承 `InterruptAgentFlow`）。

run() 捕获后，会把异常携带的 messages 加进轨迹。

一个典型的做法是塞回一条“让模型重试/纠正格式”的消息，例如：

```json
{
	"role":"assistant",
	"content":"格式错误：actions 必须是列表，请按规范输出。",
	"extra":{"exit_status":"FormatError"}
}
```

然后循环继续（除非 FormatError 的实现选择直接给 exit）。

> 你可以把它理解成：**“格式错”不是普通 crash，而是“可恢复的控制流分支”。**
> 

---

# 例子 6：动作超时（TimeoutError）与控制流

假设模型让你跑一个很慢的命令：

```json
{"tool":"bash","command":"pytest -q"}
```

环境执行超过超时阈值，`env.execute()` 抛 `TimeoutError(继承 InterruptAgentFlow)`，run() 捕获后把消息写进轨迹，例如：

```json
{"role":"assistant",
 "content":"命令执行超时，请缩小范围或增加超时时间。",
 "extra":{"exit_status":"TimeoutError"}}
```

然后继续下一轮 step：模型会看到“超时”观测/提示，从而决定换策略（例如只跑单测、或先定位文件）。

---

# 例子 7：你自己改控制流（最常见：重写 execute_actions 做拦截）

## 场景：禁止危险命令（比如 rm -rf）

你重写 `execute_actions`，在真正调用 `env.execute` 前检查 action：

```python
class SafeAgent(DefaultAgent):
    def execute_actions(self, message: dict):
        for action in message.get("extra", {}).get("actions", []):
            cmd = action.get("command", "")
            if "rm -rf" in cmd:
                # 用 InterruptAgentFlow 的子类（如 FormatError）把“可恢复提示”写回轨迹
                from minisweagent.exceptions import FormatError
                raise FormatError([{
                    "role":"assistant",
                    "content":"拒绝执行危险命令：rm -rf。请改用更安全的操作。",
                    "extra":{"exit_status":"FormatError"}
                }])
        return super().execute_actions(message)
```

### 控制流效果

- 模型输出危险 action
- agent 在 execute_actions 阶段拦截 → 抛异常
- run 捕获异常 → 把提示 message 加到轨迹
- 下一轮模型看到提示 → 改用安全命令

> 这就是文档里说的：**用异常承载 messages 的控制流，比层层传 flag 更好扩展。**
> 

---

如果你愿意，我还可以把上述例子变成一个**完全可运行的“FakeModel + FakeEnv”小脚本**（不依赖真实 API），跑出来的 messages 会和你看到的这些 JSON 结构一致，这样你就能用 debugger/print 直观看到每一步 messages 如何增长。

---

# 真正“会用”的关键：你如何改控制流（最实用的 3 类改法）

下面这些都属于“用 Python bindings 改 agent scaffold”，cookbook 给了很贴近实战的模式。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/cookbook/))

## 1) 在执行前做拦截/改写：重写 execute_actions（最常用）

### A. 让某类 action 走你自己的 Python 函数

你可以在 `execute_actions` 里识别特殊 command 前缀，然后直接生成 observation messages。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/cookbook/))

### B. 自定义“提交即退出”（不用等环境魔法字符串）

cookbook 给了两种写法：

- **Agent 侧**：遇到 `command=="submit"` 直接 `raise Submitted(...)`。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/cookbook/))
- **Environment 侧**：在 `execute()` 里遇到 submit 就抛 Submitted。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/cookbook/))

这两种的差别是：

- 放在 Agent：你更像是在改“策略层”。
- 放在 Env：你更像是在改“运行时语义/协议”。

## 2) 做安全/合规：执行前校验 action（阻断危险命令）

cookbook 示例：扩展一个 config（加 forbidden_patterns），然后在 `execute_actions` 里匹配到就抛 `FormatError` 并把提示写回对话。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/cookbook/))

## 3) 换执行后端：同一个 Agent，接不同 Environment

同一份 agent 策略，切换 `LocalEnvironment / DockerEnvironment / Singularity...` 就能实现“本地调试 vs 隔离评测”的切换。([Mini Swe Agent](https://mini-swe-agent.com/v2/advanced/environments/))

---

如果你愿意，我可以基于你现在最关心的场景（比如：**批量跑 SWE-bench、加人工审批、加入自定义工具/函数、或做更强的安全拦截**），给你一份“最小可复用的脚手架”代码，把 `DefaultAgent` 的控制流改成你想要的样子（仍然保持 mini 的简单）。