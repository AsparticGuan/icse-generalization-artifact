# Output files

[Trajectory inspector - SWE-agent documentation](https://tse2.mm.bing.net/th/id/OIP.4a0bZtsY2EaKHR1bobd3iwHaGP?pid=Api)

# Output files 是什么、为什么你会在意

mini-SWE-agent 会把**一次 run 的结果**保存成 JSON 文件，主要有两类：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

- **轨迹文件**：`.traj.json`
    
    记录“这次 run 从开始到结束发生了什么”：完整对话、每一步执行的命令、命令输出、配置、成本、最终提交的 patch 等。([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))
    
- **预测聚合文件**：`preds.json`
    
    主要给 **SWE-bench 评测**用：把多条实例（instance）的最终 patch 汇总到一个文件里，便于评测脚本读取。([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))
    

一句话：

- `.traj.json` 适合 **debug / 复盘 / 审计 / 统计成本**
- `preds.json` 适合 **批量跑 benchmark 后交评测**

---

# `.traj.json`：什么时候生成、保存在哪里、怎么改路径

## 默认保存位置（以 `mini` 命令为例）

`mini` 默认会把轨迹保存到全局配置目录下的：

- `global_config_dir / "last_mini_run.traj.json"`([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/mini/))

> 这就是你每次跑完 `mini`，都会有一个“上一次 run 的完整历史”的原因。
> 

## 你如何指定输出路径

`mini` 提供 `-o/--output` 参数，并把它写进 agent 配置的 `output_path`：([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/mini/))

```bash
mini -o /tmp/my_run.traj.json

```

你也可以在你自己的脚本里调用保存逻辑。比如官方的 GitHub Issue 运行脚本里，在 `finally` 里确保无论正常结束还是 Ctrl+C 都会保存：([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/github_issue/))

```python
save_traj(agent, Path("traj.json"), exit_status=exit_status, result=result)

```

---

# `.traj.json`：怎么“看”最舒服（实用）

## 用官方 Inspector（推荐）

官方文档建议用 `inspector` 浏览 `.traj.json`：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

```bash
mini-extra inspector
# 或文档里提到的快捷方式：mini-e i

```

它会给你一个可交互界面：按步骤查看每次模型回复、解析出的命令、命令输出、最终提交等。([mini-swe-agent.com](https://mini-swe-agent.com/latest/usage/inspector/))

## 用命令行 JSON 浏览器（轻量）

文档也提到 `jless` 很适合在终端里看这种大 JSON：([mini-swe-agent.com](https://mini-swe-agent.com/latest/usage/inspector/))

---

# `.traj.json` 的结构：每个字段到底拿来干嘛

轨迹文件的顶层长这样：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

```json
{
  "info": { ... },
  "messages": [ ... ],
  "trajectory_format": "mini-swe-agent-1.1"
}

```

## `trajectory_format`：格式版本号

- v2.0 起格式改了，`trajectory_format` 会标成 `mini-swe-agent-1.1`，用于区分/迁移兼容。([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

## `info`：这次 run 的“摘要信息”

里面最常用的是：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

- `info.model_stats.instance_cost`：本次 run API 总成本
- `info.model_stats.api_calls`：本次 run 调了多少次模型
- `info.config`：本次 run 用的 agent/model/environment 配置（以及它们的类名 type）
- `info.mini_version`：mini-SWE-agent 版本
- `info.exit_status`：最终状态（例如 `Submitted`, `LimitsExceeded` 等）
- `info.submission`：最终提交内容（通常是 unified diff patch；如果有提交的话）

> 通俗理解：`info` 就是“封面页”，你不用翻完整对话也能先看结论、成本、配置、最终 patch。
> 

## `messages`：完整的“对话 + 工具执行历史”

`messages` 基本遵循 OpenAI chat message 结构（`role/content`），但**额外多了 `extra` 字段**，mini-SWE-agent 把关键元数据都塞在这里。([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

## 1）assistant 消息里：你真正关心的通常是 `extra.actions`

示例（文档原型）：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

- `content` 里包含模型输出（例如它写了一个 `mswea_bash_command` 代码块）
- `extra.actions` 是**解析器从 content 里抽出来的可执行命令列表**
- `extra.cost / timestamp / response` 方便你做成本统计、时间线、或保留原始 API 回包

这解释了一个关键点：

> 模型“说的话”（content）和系统“要执行的命令”（extra.actions）是分开的；后者是从前者里解析出来并结构化保存的。
> 

## 2）observation（命令输出）消息里：关心 `returncode` 和输出内容

文档示例里 observation 以 `role: "user"` 保存，并在 `extra` 里放：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

- `extra.returncode`
- `extra.timestamp`

而输出文本在 `content`（可能是带标签的序列化文本）。

---

# 从 `.traj.json` 里“直接拿结果”的几个小例子

> 下面用法不依赖 inspector，适合脚本化处理。
> 

## 例 1：取出最终 exit_status 和 submission（最终 patch）

```bash
python - <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p))
print("exit_status:", data["info"].get("exit_status"))
sub = data["info"].get("submission")
print("has_submission:", bool(sub))
if sub:
    print(sub[:500], "\n... (truncated)")
PY last_mini_run.traj.json

```

对应字段定义见文档结构：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

## 例 2：统计本次 run 成本与 API 调用次数

```bash
python - <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
ms=d["info"]["model_stats"]
print("instance_cost =", ms.get("instance_cost"))
print("api_calls      =", ms.get("api_calls"))
PY last_mini_run.traj.json

```

字段含义见：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

## 例 3：列出本次 run 执行过的所有 bash 命令（按出现顺序）

```bash
python - <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
for m in d["messages"]:
    extra = m.get("extra") or {}
    for a in extra.get("actions", []):
        print(a.get("command"))
PY last_mini_run.traj.json

```

`extra.actions` 的定义与示例见：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

---

# Toolcall 模型时的差异（别被 role 搞懵）

如果你用的是“工具调用风格”的模型（例如 `LitellmToolcallModel` 这类），文档提醒：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

- assistant 消息可能主要是 `tool_calls`（而不是把命令写在 `content`）
- 工具执行结果消息会用 `role: "tool"`，并带 `tool_call_id`

**所以你写解析脚本时，别只假设 observation 一定是 `role: "user"`；要兼容 `role: "tool"`。([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))**

---

## `preds.json`：什么时候会有、怎么用

`preds.json` 是给 SWE-bench 评测兼容的聚合文件，结构是：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

```json
{
  "owner__repo__123": {
    "model_name_or_path": "...",
    "instance_id": "owner__repo__123",
    "model_patch": "diff --git a/file.py b/file.py\n..."
  }
}

```

## 它的用法（最常见）

- 你跑一堆 SWE-bench 实例（批量/多 worker）
- 每个实例都会有自己的 `.traj.json`（用于复盘）
- 同时把每个实例的最终 `model_patch` 汇总进 `preds.json`，给评测工具一次性读取([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

## 例：从 preds.json 里取某个 instance 的 patch

```bash
python - <<'PY'
import json, sys
preds=json.load(open(sys.argv[1]))
instance_id=sys.argv[2]
print(preds[instance_id]["model_patch"][:500], "\n... (truncated)")
PY preds.json owner__repo__123

```

---

# 一个很现实的提醒：分享/上传前先过一眼

因为 `.traj.json` 里包含：

- 完整对话（可能有你粘贴的代码/日志）
- 原始响应片段（`extra.response`）
- 环境/配置细节

# 用几个“真实小故事”理解 Output files（.traj.json / preds.json）

下面每个例子都尽量贴近你真的会怎么用：

**跑一次、复盘一次、批量评测一次、查一次成本**。字段名都对应官方格式。([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

---

## 例 1：你跑了一次 mini，想“回放全过程”（最常见）

### 你做了什么

```bash
mini -o /tmp/my_run.traj.json

```

- `o/--output` 会把这次 run 的轨迹写到你指定的文件（否则默认会写到全局配置目录下的 `last_mini_run.traj.json`）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/mini/?utm_source=chatgpt.com))

### 你能得到什么

打开 `/tmp/my_run.traj.json` 你会看到三块核心内容：`info`（摘要）、`messages`（全过程）、`trajectory_format`（版本号）。([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

### 你怎么“看”最舒服

- 用 inspector 交互式浏览：
    
    ```bash
    mini-extra inspector
    # 或 mini-e i
    
    ```
    
    inspector 的定位就是“浏览 .traj.json 的 run 历史”。([mini-swe-agent.com](https://mini-swe-agent.com/latest/usage/inspector/?utm_source=chatgpt.com))
    

> 直觉类比：`.traj.json` 像“飞行记录仪”，inspector 是“回放播放器”。
> 

---

## 例 2：你想知道“模型说的话”和“真正执行的命令”怎么对应（解释 `extra.actions` 的设计）

### 场景

模型输出里写了一个 bash 代码块：

```json
{
  "role": "assistant",
  "content": "Let me check the files...\n\n```mswea_bash_command\nls -la\n```",
  "extra": {
    "actions": [{"command": "ls -la"}]
  }
}

```

这里的关键设计是：

- `content`：给人看的自然语言 + 代码块
- `extra.actions`：给系统看的**结构化动作**（解析器从 `content` 里抽取出来，确保“可执行/可复盘/可统计”）([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

### 这个设计解决了什么问题

- 你不需要“从一大段文本里猜命令是什么”，直接信 `extra.actions`
- 你可以脚本化地把所有命令提取出来做审计 / 回放 / 统计（例如统计执行了多少次 `pytest`）

---

## 例 3：某条命令失败了，你要定位“从哪一步开始崩”（解释 observation 结构）

### 场景

假设执行测试失败，轨迹里会有一条“执行结果/观测”的 message，包含：

- `extra.returncode`：退出码
- `content`：stdout/stderr（文档示例用类似 `<returncode>` `<output>` 包起来）([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

示意（把成功的 returncode 改成失败更贴近真实）：

```json
{
  "role": "user",
  "content": "<returncode>1</returncode>\n<output>\nFAILED test_xxx ...\n</output>",
  "extra": { "returncode": 1 }
}

```

### 你怎么用它排查

- 在 inspector 里按 step 看：**哪一步的 returncode 从 0 变成 1**
- 回到上一条 assistant message：看它的 `extra.actions` 执行了什么命令（比如 `pytest -q`）
- 再看更上一条：模型为什么决定这么做（`content`）

> 直觉类比：每一步都是“我打算做什么（actions）→ 我做完看到了什么（observation）”。
> 

---

## 例 4：run 结束时到底“提交了什么”（解释 exit_status / submission 在哪）

### 场景：成功提交（Submitted）

轨迹里会在 `info` 里给一个“摘要版结论”：

- `info.exit_status`
- `info.submission`（最终 patch / 输出）([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

同时，在 message 流的最后，还会有一条 final message，把同样的结论放在 `extra` 里：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

```json
{
  "role": "user",
  "content": "",
  "extra": {
    "exit_status": "Submitted",
    "submission": "diff --git a/file.py..."
  }
}

```

### 为什么要“两处都放”

- `info`：像“封面摘要”，你不用翻对话就能拿到最终结果
- `messages`：像“时间线的一部分”，保证整个 run 的事件流是完整、顺序、可回放的([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

---

## 例 5：你用的是 toolcall 模型，为什么会看到 `role: "tool"`（解释 toolcall 变体）

### 场景

文档明确说：toolcall-based 模型会有两点差异：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

- assistant message 可能主要是 `tool_calls`（不一定把动作写进 `content`）
- 执行结果 message 会用 `role: "tool"`，并带 `tool_call_id`

### 你写解析脚本时该怎么想

- **别只认** `role: "user"` 才是 observation
- 兼容 `role: "tool"` 才能覆盖 toolcall 模型的轨迹格式([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

---

## 例 6：你在跑 SWE-bench 批量评测：每题一个 traj + 一个 preds.json 汇总（解释 preds.json 的用途）

### 场景

你跑了很多个 instance：

- 每个 instance 都会有自己的 `.traj.json`（方便复盘、查失败原因）
- 同时会生成一个 `preds.json`，把“每题最终 patch”汇总成 SWE-bench 兼容格式：([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

示意（最核心三字段）：

```json
{
  "owner__repo__123": {
    "model_name_or_path": "...",
    "instance_id": "owner__repo__123",
    "model_patch": "diff --git a/file.py b/file.py\n..."
  }
}

```

### 你怎么用它们配合排查

- SWE-bench 评测脚本主要吃 `preds.json`（因为它只关心“最终 patch”）([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))
- 但如果某个 instance 的 `model_patch` 是空的/怪的：
    
    你就去打开该 instance 对应的 `.traj.json`，看 `info.exit_status` 是不是 `LimitsExceeded`、或者中途反复失败在哪一步（例 3 的方法）。([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))
    

---

## 例 7：你觉得这次 run “太贵了”，想知道钱花在哪一步（解释 model_stats / per-message cost）

### 你先看总账

`info.model_stats.instance_cost` 和 `info.model_stats.api_calls` 给你总成本和调用次数。([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

### 再看明细

每条 assistant message 的 `extra.cost` 是“这一次模型调用的成本”，你可以用它做：

- 找出最贵的那几步（通常是长上下文/长输出）
- 计算平均每次调用成本（instance_cost / api_calls）
- 看是不是某一步陷入循环导致调用数飙升([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

---

## 例 8：你在做 v2 迁移/对比，想确认格式版本（解释 trajectory_format）

### 场景

v2.0 输出格式变更后，会在轨迹里标记：

- `trajectory_format: mini-swe-agent-1.1`([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

### 你怎么用它

- 你的解析/可视化工具可以先读这个字段，决定用哪套解析逻辑
- 当你团队里有人拿旧轨迹文件来跑新工具时，这个字段能帮你快速判断“是不是格式不匹配”([mini-swe-agent.com](https://mini-swe-agent.com/v2/usage/output_files/))

---