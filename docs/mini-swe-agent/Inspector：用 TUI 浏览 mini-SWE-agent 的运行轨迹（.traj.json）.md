# Inspector：用 TUI 浏览 mini-SWE-agent 的运行轨迹（.traj.json）

[mini-swe-agent · PyPI](https://tse2.mm.bing.net/th/id/OIP.51Aky9-zzIrkTR7e3eP4QQHaFS?pid=Api)

# 1. 它是什么、解决什么问题

**Inspector** 是一个“终端里的轨迹浏览器”（TUI），用来打开 mini-SWE-agent 运行后产生的 `*.traj.json`，把这次 run 的对话、工具调用、工具输出等**按步骤翻页**展示出来，方便你复盘“agent 到底做了什么”。([mini-swe-agent.com](https://mini-swe-agent.com/latest/usage/inspector/))

你可以把它理解成：

> 给 agent 的“录像回放 + 翻页阅读器”（而不是让你直接在 JSON 里游泳）。
> 

文档里也提到：如果你更喜欢命令行 JSON 浏览器，`jless` 是个很好的替代/补充。([mini-swe-agent.com](https://mini-swe-agent.com/latest/usage/inspector/))

# 2. 你在 Inspector 里会看到什么

Inspector 打开后，核心就是三层概念：

- **Trajectory（轨迹）**：一个 `.traj.json` 文件 = 一次 run 的完整历史
- **Step（步骤/页）**：Inspector 为了好读，会把很多条 message **分组**成一页一页
- **Message（消息）**：具体的一条记录（user/assistant/tool 等）

界面标题会显示类似：

- `Trajectory 1/N - <文件名> - Step 3/M`（当前第几个轨迹文件、当前第几页）([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

每个 Step 内部会按 message 展示：

上面是角色（`USER / ASSISTANT / TOOL` 等），下面是该条消息的内容字符串（由 `get_content_string` 统一格式化）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

# 3. 怎么启动：最常用的 3 种方式

> 记住一句：**不带参数就从当前目录递归找 `*.traj.json`**。
> 

## 1) 从当前目录递归搜索所有 .traj.json 并打开

```bash
mini-extra inspector
```

## 或短命令

```bash
mini-e i
```

## 2) 直接打开某一个轨迹文件

```bash
mini-e i path/to/something.traj.json
```

## 3) 只在某个目录下搜索（避免全盘扫）

```bash
mini-e i path/to/a/directory
```

以上用法与行为在文档里写得很明确。([mini-swe-agent.com](https://mini-swe-agent.com/latest/usage/inspector/))

实现上也很直白：

传入的是文件就只打开这个文件；传入目录就 `rglob("*.traj.json")` 并排序；啥也没有就报错。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

# 4. “Step” 到底怎么切页（结合实现讲清楚）

Inspector 的 Step 不是“模型的一次 token 输出”，而是它**人为分组出来的一页**，规则在 `_messages_to_steps` 里：([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

- 遇到 **assistant 消息**：开新的一步
- 或遇到 message 里带 `extra.actions`（表示有工具调用相关信息）：也开新的一步
- 其他消息就继续塞进当前 step

用一个极简示意（不完全等同真实字段，只看结构）：

```
messages:
  1) user: “修一下 bug”
  2) assistant: “我先看看文件结构…”
  3) tool: ls / cat ...
  4) assistant: “我改下代码…（可能又触发 actions）”
  5) tool: apply_patch ...
  6) assistant: “跑测试…”
  7) tool: pytest ...

```

Inspector 更希望你看到的“页”大致会是：

- Step 1：user 提问
- Step 2：assistant 的一次推进（以及紧随其后的相关内容）
- Step 3：assistant 的下一次推进…
- …

这能让你用 `h/l` 像翻书一样回看 agent 的推进节奏，而不是在一长串 JSON 里找“这一轮到底干了啥”。

# 5. 键位怎么用（+ 实现里还有 3 个很实用的“额外键”）

文档列出的常用键位：([mini-swe-agent.com](https://mini-swe-agent.com/latest/usage/inspector/))

- `q`：退出
- `h` / `LEFT`：上一步（上一页）
- `l` / `RIGHT`：下一步（下一页）
- `j` / `DOWN`：向下滚动
- `k` / `UP`：向上滚动
- `H`：上一个 trajectory 文件
- `L`：下一个 trajectory 文件
- `e`：把“当前 step”丢到 `jless` 打开

实现里还额外绑定了（文档没写，但很好用）：([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

- `0`：跳到第一步（Step=0）
- `$`：跳到最后一步（Step=-1）
- `E`：用 `jless` 打开**整个 trajectory 文件**（不是只开当前 step）

# 6. 三个“很实际”的使用例子

## 6.1 例子 A：我只想快速复盘这一次 run 干了啥

你在产物目录（或项目根目录）直接：

```bash
mini-e i
```

然后：

1. `l` 一页页往后翻，看 assistant 每一步的决策
2. 遇到“测试失败/命令报错”的 step，用 `j/k` 在该页里上下滚，找到关键输出
3. 想直接跳到结尾看最终结果：按 `$`([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

## 6.2 例子 B：我有很多次 run，想对比哪次做对了

假设你某个目录里有多份 `*.traj.json`：

```bash
mini-e i path/to/runs/
```

打开后：

- 用 `H/L` 在不同轨迹文件之间切换（比如对比“同一个任务，不同 prompt/不同模型”的差异）([mini-swe-agent.com](https://mini-swe-agent.com/latest/usage/inspector/))
- 每个轨迹里再用 `h/l` 看它推进的关键转折点

## 6.3 例子 C：这一页信息太多，我想“搜索”错误关键词

在 Inspector 某一步停住，按：

- `e`：只把**当前 step**导出成临时 JSON，交给 `jless` 打开([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))
- 在 `jless` 里用它的搜索/浏览能力（更适合查找关键词、看层级结构）

或者按：

- `E`：直接把整个轨迹文件交给 `jless`（适合全局搜）([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

# 7. 为什么 `e` 很有用：它到底做了什么（实现细节）

按下 `e` 以后，Inspector 会：([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

1. 把“当前 step 的 messages 列表”写到一个临时 `.json` 文件（带缩进）
2. `subprocess.run(["jless", 临时文件])`
3. 退出 `jless` 后把临时文件删掉

所以 `e` 的价值是：

> 你在 Inspector 里“翻页定位”，在 `jless` 里“结构化浏览 + 搜索”。
> 

另外，`jless` 本身支持不同展示模式，比如按 `m` 切到 pretty printed 的 line mode。([jless.io](https://jless.io/user-guide))

# 8. 还有两个小技巧

## 8.1 屏幕上怎么选中文本复制？

按住 **Alt/Option** 再用鼠标选择。([mini-swe-agent.com](https://mini-swe-agent.com/latest/usage/inspector/))

## 8.2 我想改 Inspector 的样式（CSS）

Inspector 启动时会读取 `MSWEA_INSPECTOR_STYLE_PATH` 指向的样式文件；不设就用默认的 `inspector.tcss`。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

# 9. 你可能会踩的坑（以及怎么判断）

- **提示找不到 traj 文件**：说明你给的目录下递归也没找到 `.traj.json`，实现会直接抛 “No trajectory files found”。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))
- **按 `e` 没反应/报错**：大概率是没装 `jless`（实现里会提示 “jless not found … brew install jless”）。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))
- **切 step 后滚动位置怪**：实现里每次切 step/trajectory 都会把滚动条重置到顶部，避免你“翻页后还停在中间”。([mini-swe-agent.com](https://mini-swe-agent.com/latest/reference/run/inspector/))

# 用“实际、简单的例子”理解 Inspector 的内容与设计

下面我用几段**非常小的 `.traj.json` 片段**，

配合你能在 Inspector 里按的键（`h/l/j/k/H/L/e/E/0/$`），把我前面提到的“Step 分页、轨迹切换、jless 打开”等设计讲透。

---

## 例子 1：最典型的一轮——“提问 → 回答 → 调工具 → 再回答”

假设你的 `run.traj.json`（极简示意）长这样：

```json
{
  "messages": [
    { "role": "user", "content": "把 README 里的拼写错别字修一下" },

    { "role": "assistant", "content": "我先打开 README 看看。" },

    { "role": "tool", "name": "bash", "content": "cat README.md\n...Teh project..." },

    { "role": "assistant", "content": "发现 'Teh' 应该是 'The'，我来修复。" },

    { "role": "tool", "name": "bash", "content": "apply_patch ...\nDone!" },

    { "role": "assistant", "content": "已修复并提交。你再确认一下。" }
  ]
}

```

### Inspector 里会被分成哪些 Step（页）？

Inspector 的分页规则是（来自 `_messages_to_steps`）：

- **遇到 `assistant` 消息就“开新页”（把后续相关消息归到这页里/或形成下一页的起点）**
- **遇到 `extra.actions` 也会触发“开新页”（后面例子会演示）**

结合上面这段 messages，**你在 Inspector 里大体会看到类似分页：**

### Step 1

- (1) `USER`：把 README 里的拼写错别字修一下

### Step 2

- (2) `ASSISTANT`：我先打开 README 看看。
- (3) `TOOL`：cat README.md\n...Teh project...

### Step 3

- (4) `ASSISTANT`：发现 'Teh' 应该是 'The'，我来修复。
- (5) `TOOL`：apply_patch ...\nDone!

### Step 4

- (6) `ASSISTANT`：已修复并提交。你再确认一下。

> 你用 `l/RIGHT` 就是在“看 agent 推进的节奏”，每按一次是“下一步推理/执行阶段”。
> 

### 你怎么用键快速复盘？

- `l`：下一页，看 agent 下一步做了什么
- `h`：上一页，回去看“为什么会做这一步”
- `j/k`：当某页里工具输出很长（例如 `pytest` 输出）时上下滚动
- `$`：直接跳到最后一步，看最终结果（实现里确实绑定了 `$`）
- `0`：回到第一步，从头复盘（实现里绑定了 `0`）

---

## 例子 2：为什么要有 “Step” 这种分页——工具输出太长，必须“按阶段读”

想象一次真实 debug：测试失败输出几十上百行。

```json
{
  "messages": [
    { "role": "user", "content": "修复单测失败" },

    { "role": "assistant", "content": "我先跑一下测试定位问题。" },

    { "role": "tool", "name": "bash", "content": "pytest -q\n... (很多行错误栈) ..." },

    { "role": "assistant", "content": "报错点在 foo.py 的 parse()，我去看源码。" },

    { "role": "tool", "name": "bash", "content": "sed -n '1,200p' foo.py\n...(源码很多行)..." },

    { "role": "assistant", "content": "我修复了边界条件，重新跑测试。" },

    { "role": "tool", "name": "bash", "content": "pytest -q\nOK" }
  ]
}

```

### Inspector 的阅读方式（非常符合 debug）

1. 用 `l` 翻到 “pytest 输出那一页”
2. 在那一页里用 `j/k` 滚动，找到关键报错行（比如 assertion 或 trace 起点）
3. `l` 到下一页，看 assistant 如何解释/定位
4. 重复：看源码 → 看补丁 → 看复测

> 这就是 Step 的价值：你不会在一个“超长 JSON 文本”里迷路，而是按“定位 → 分析 → 修改 → 验证”的阶段翻页。
> 

---

## 例子 3：`e` 为什么特别好用——“把当前 Step 丢进 jless 搜索”

当你停在某个 Step（例如 pytest 输出那页），按：

- `e`：只把**当前 Step 的 messages**导出为一个临时 JSON，然后调用 `jless <temp.json>`
- `E`：直接 `jless` 打开整个 trajectory 文件

### 一个很实际的场景

你在 Inspector 里翻到 Step 3，看到了超长报错输出，但你想在这一步里**搜索关键词**（比如 “AssertionError” 或某个文件名）。

- 在 Inspector：按 `e`
- 在 `jless`：用它的搜索（按 `/` 一般可搜索，具体看你的 jless 使用习惯）
- 搜到后退出 `jless`，你回到 Inspector 继续 `l/h` 翻页

> 设计含义：Inspector 负责“按阶段导航”，jless 负责“结构化浏览 + 搜索”。
> 

---

## 例子 4：`extra.actions` 触发分页——“工具调用是一个阶段边界”

实现里还有一条分页规则：

**只要 message 里带 `message["extra"]["actions"]`，就会触发新 Step。**

你可以把它理解成：

> “发生工具动作（action）时，往往意味着进入了一个新的执行阶段，所以应该换页。”
> 

极简示意（字段只保留重点）：

```json
{
  "messages": [
		# Step1
    { "role": "user", "content": "把 a.py 里的 bug 修了" },
		# Step2
    {
      "role": "assistant",
      "content": "我先列一下目录。",
      "extra": { "actions": [{ "tool": "bash", "command": "ls" }] }
    },
		# Step3
    { "role": "tool", "name": "bash", "content": "a.py\nb.py\n..." },
		# Step4
    { "role": "assistant", "content": "我去打开 a.py 看实现。" }
  ]
}

```

**这里 `assistant` 那条消息同时带了 `extra.actions`**，在 Inspector 看起来就更像：

- 一个“准备执行工具动作”的阶段
- 紧跟着工具输出
- 再进入下一步分析

### Message 1

```
{ "role": "user", "content": "把 a.py 里的 bug 修了" }
```

- 不是 assistant、也没有 actions
- → 追加到 `current_step`

当前 `current_step`：

- [ user ]

---

### Message 2

```
{
  "role": "assistant",
  "content": "我先列一下目录。",
  "extra": { "actions": [...] }
}
```

- 是 assistant（同时也有 actions，但不影响：**条件是 OR**）
- → **先把当前 `current_step`（里面只有 user）作为一个 Step 推入**
- → 然后新开 `current_step = [这条 assistant]`

所以得到：

**Step 1**

- user: 把 a.py 里的 bug 修了

当前 `current_step`：

- [ assistant(列目录，有 actions) ]

---

### Message 3

```
{ "role": "tool", "name": "bash", "content": "a.py\nb.py\n..." }
```

- 不是 assistant、也没有 actions
- → 追加到当前 `current_step`

当前 `current_step`：

- [ assistant(列目录…), tool(bash 输出…) ]

---

### Message 4

```
{ "role": "assistant", "content": "我去打开 a.py 看实现。" }
```

- 是 assistant
- → **先把当前 `current_step`（assistant+tool）推入 steps**
- → 然后新开 `current_step = [这条 assistant]`

所以得到：

**Step 2**

- assistant: 我先列一下目录。（带 actions）
- tool(bash): a.py / b.py / …

循环结束后还有 `current_step`，再 push：

**Step 3**

- assistant: 我去打开 a.py 看实现。

---

## 最终分页结果（按 UI 的 Step 1/2/3）

- **Step 1**：`[ user ]`
- **Step 2**：`[ assistant(列目录 + actions), tool(bash 输出) ]`
- **Step 3**：`[ assistant(打开 a.py) ]`

> 关键理解：
**assistant（或 actions）会“切断”上一页，并把自己作为新页的开头；
工具输出通常会跟在触发它的那条 assistant 同一页里，直到下一个 assistant/actions 再切页。**
> 

---

## 例子 5：同目录多份 `.traj.json`——用 `H/L` 像翻相册一样对比两次 run

假设你的目录里有：

```
runs/
  fix_bug_A.traj.json
  fix_bug_A_retry.traj.json
  fix_bug_A_new_prompt.traj.json

```

你运行：

```bash
mini-e i runs/
```

然后你可以：

- `L`：切到下一份轨迹文件（比如从 `fix_bug_A` → `fix_bug_A_retry`）
- `H`：切到上一份轨迹文件
- 每份轨迹里用 `0/$` 快速跳到开头/结尾
- 用 `h/l` 在关键步骤附近对比：
    - 第一次 run 是否卡在某个错误循环？
    - retry 是否在“定位步骤”就改进了？
    - new prompt 是否让决策更稳？

> 这是 Inspector 在“多 run 复盘/对比”上的核心价值：它把“轨迹文件”变成可浏览的序列。
> 

---

## 例子 6：为什么切 Step/切 Trajectory 后会自动回到顶部？

实现里切换 `i_step` 或 `i_trajectory` 时会把滚动位置 `scroll_to(y=0)`。

一个很实际的痛点：

- 你在 Step 3 里向下滚了很久（因为 pytest 输出超长）
- 如果切到 Step 4 还停留在“很下面的位置”，你会一脸懵：怎么一翻页就空了/断片了？

自动回顶部就是为了让“翻页像翻书”：

> 新的一页，从页顶开始读，不会继承上一页的滚动偏移。
> 

---