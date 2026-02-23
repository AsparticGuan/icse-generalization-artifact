# auto-opt / pipeline 使用说明（详细版）

> 本文档基于以下脚本代码整理：
>
> - `pipeline/localize.py`
> - `pipeline/generate.py`
> - `pipeline/generate_orig.py`
> - `pipeline/generalize.py`
> - `pipeline/retest_patches.py`

---

## 1. 目录定位与职责

`pipeline/` 目录是一个围绕 **LLVM missed optimization 自动修复** 的流水线集合，核心流程包括：

1. **定位（localize）**：先让模型猜测应该修改的 InstCombine 文件和函数。
2. **补丁生成（generate / generate_orig）**：让模型在目标函数附近改 C++ 代码，反复编译与验证。
3. **补丁复测（retest_patches）**：对历史 patch 统一重测，判断可复现性。
4. **模式泛化（generalize）**：从单一 missed optimization 示例扩展成更多测试样例并验证。

---

## 2. 脚本总览

| 脚本 | 主要用途 | 典型输入 | 典型输出 |
|---|---|---|---|
| `localize.py` | 两阶段定位：先文件、再函数 | 数据集 issue、LLVM 源码、可选 opt debug 输出 | `results/localize/<model>.json` |
| `generate.py` | 普通数据集补丁生成（含 FileCheck 语义） | issue、定位结果（可选）、LLM | `LAB_FIX_DIR/<issue>.json` |
| `generate_orig.py` | `-orig` 数据集补丁生成（无 FileCheck，走 `verify_test_group_orig`） | orig issue、定位结果（可选）、LLM | `LAB_FIX_DIR_ORIG/<issue>-orig.json` |
| `retest_patches.py` | 对已有 patch 重新应用+check_fast 验证 | patch JSON 目录 + dataset | `LAB_RETEST_DIR/<issue>.json` |
| `generalize.py` | 从单个 missed-opt 示例生成泛化样例并验证 | dataset 首个可用样例 + LLM | `results/generalize/<model>.json` |

---

## 3. 推荐执行顺序

### 标准主线

1. `python pipeline/localize.py`（可选但强烈建议）
2. `python pipeline/generate.py <issue_id> -f`（普通样本）
3. 或 `python pipeline/generate_orig.py <issue_id>-orig -f`（orig 样本）
4. `python pipeline/retest_patches.py <issue_id> -f`（批量复测）

### 分析增强支线

- `python pipeline/generalize.py`
  - 用于把一个 case 扩展成多个结构变体，辅助理解 missed optimization 的规律与边界条件。

---

## 4. 运行前准备

## 4.1 Python 依赖

至少需要：

- `openai`
- `tqdm`（`localize.py`）
- `tree_sitter` + `tree_sitter_cpp`（`generate*.py` 可选；没有时会降级）

建议：

```bash
pip install openai tqdm tree-sitter tree-sitter-cpp
```

## 4.2 LLVM / Alive2 / cost tool 环境

这些脚本都依赖 `scripts/llvm_helper.py` 与 `scripts/lab_env.py`，通常需要先初始化环境（例如 `source init_env.sh`）。

尤其 `retest_patches.py` 会显式检查以下变量是否存在：

- `LAB_LLVM_DIR`
- `LAB_LLVM_BUILD_DIR`
- `LAB_LLVM_ALIVE_TV`
- `LAB_LLVM_COST_TOOL`
- `LAB_DATASET_DIR`

缺失则直接退出。

## 4.3 LLM 访问

- `generate.py`/`generate_orig.py` 默认使用 `LAB_LLM_TOKEN`（必需）
- `localize.py`/`generalize.py` 若不传 `LAB_LLM_TOKEN`，会尝试使用 OpenAI 默认配置
- 默认模型（若未覆盖）并不完全一致：
  - `generate*.py` 默认：`deepseek-reasoner`
  - `localize.py` / `generalize.py` 默认：`qwen/qwen3-235b-a22b-2507`

---

## 5. 数据结构约定（核心）

这些脚本默认读取 `LAB_DATASET_DIR` 下的 `<issue_id>.json`，并依赖如下字段（按使用频率排序）：

- `tests`：测试集合
  - 每个 test 通常包含：
    - `source_program`
    - `current_optimized_program`
    - `expect_optimized_program`
    - `test_name`
- `hints`
  - `components`
  - `bug_location_funcname`（localize 用于计算 gold）
- `lit_test_dir`（full check 需要）
- `verified`（是否已验证可用于修复流程）

如果字段缺失，不同脚本会表现为跳过、警告、或者反馈质量下降。

---

## 6. `localize.py` 详解

## 6.1 作用

`localize.py` 做两件事：

1. **文件定位**：从固定候选 InstCombine 文件列表中挑 Top-3。
2. **函数定位**：对每个预测文件，读取源码并再挑 Top-3 函数。

## 6.2 候选文件范围

候选是硬编码的一组 InstCombine 源文件（`InstCombineAddSub.cpp`、`InstCombineCasts.cpp`、`InstructionCombining.cpp` 等），不是全 LLVM 搜索。

这意味着：

- 速度更快，token 更可控。
- 如果 bug 在候选之外，召回率受限。

## 6.3 输入与流程

每个 issue 的流程：

1. `Env(issue_id).reset()`
2. 找到一个 `current_optimized_program != expect_optimized_program` 的测试样例。
3. 构建 issue 描述（IR before/after）。
4. 可选执行 `opt -debug`，将 debug 输出拼进提示词。
5. 调模型输出 Top-3 文件（要求在 ```text 代码块中）。
6. 逐个文件读取源码，再调模型输出 Top-3 函数。
7. 写入结果 JSON，支持增量覆盖。

## 6.4 命令行

```bash
# 全量处理 dataset 目录中的 issue
python pipeline/localize.py

# 只处理指定 issue（逗号分隔）
python pipeline/localize.py 84608,85250

# 强制覆盖已存在结果
python pipeline/localize.py 84608,85250 -f
```

## 6.5 关键环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LAB_DATASET_DIR` | `dataset` | 数据集目录 |
| `LAB_LOCALIZE_OUTPUT` | `results/localize/<model>.json` | 输出文件 |
| `LAB_LLVM_DIR` | `utils/llvm/llvm-project` | LLVM 根目录（默认来源） |
| `LAB_LOCALIZE_LLVM_ROOT` | 同上 | localize 实际使用的 LLVM 根 |
| `LAB_LOCALIZE_ENABLE_OPT_DEBUG` | `ON` | 是否生成 opt debug 上下文 |
| `LAB_LOCALIZE_BUILD_BEFORE_OPT_DEBUG` | `ON` | debug 前是否先 build |
| `LAB_LOCALIZE_OPT_DEBUG_TIMEOUT` | `30` | debug 命令超时（秒） |
| `LAB_LOCALIZE_MAX_FILE_CHARS` | `200000` | 传给模型的源码截断长度 |
| `LAB_LOCALIZE_MAX_ISSUE_DESC_CHARS` | `20000` | issue 描述截断长度 |
| `LAB_LOCALIZE_MAX_DEBUG_CHARS` | `5000` | debug 输出截断长度 |

## 6.6 输出格式

输出是一个以 `issue_id` 为 key 的 JSON，典型字段：

```json
{
  "84608": {
    "pred_files": ["InstCombineAddSub.cpp", "InstructionCombining.cpp", "InstCombineCasts.cpp"],
    "pred_funcs": {
      "InstCombineAddSub.cpp": ["InstCombinerImpl::visitAdd", "...", "..."],
      "InstructionCombining.cpp": ["...", "...", "..."]
    },
    "gold_file": "llvm/lib/.../InstCombineAddSub.cpp",
    "gold_funcs": ["InstCombinerImpl::visitAdd"],
    "file_correct": true,
    "func_correct": false
  }
}
```

## 6.7 注意点

- 模型输出若不符合 ` ```text ... ``` ` 约束，解析可能失败并得到 `None`。
- 文件路径解析是按 basename 在 LLVM 根目录 `rglob`，重名文件时可能匹配到非预期位置。

---

## 7. `generate.py` 详解（普通数据集）

## 7.1 作用

面向普通（非 `-orig`）issue 的自动补丁生成器：

- 从目标函数抽取 hunk
- 让 LLM 生成替换代码
- 应用后 `check_full()` 验证
- 根据失败原因回馈模型，迭代最多 `LAB_LLM_MAX_SAMPLE_COUNT` 轮

## 7.2 修复策略核心

每轮迭代大致是：

1. `chat()` 让模型生成完整替换代码。
2. `modify_inplace(file, src, tgt)` 直接 `str.replace(src, tgt)`。
3. `env.check_full()`：编译 + fast/full 测试。
4. 根据结果构造反馈：
   - **构建失败**：提取错误日志，让模型修编译错误。
   - **优化未达成**：展示 failed test 的 source/expect/current IR，继续迭代。
   - **fast 过但 full 挂**：记录 `fast_full_mismatch_patch`，要求在保持优化的同时修回归。

同时会动态维护 `env.prompted_tests`：

- 先检查已提示过的测试是否都优化成功。
- 若都成功，则从未提示测试中新增一个失败样例，避免 prompt 无限膨胀。

## 7.3 与 `localize.py` 的联动

`generate.py` 会读取 `LAB_LOCALIZE_OUTPUT`（默认使用该环境变量）并尝试：

- 优先使用 `pred_funcs` 的 `(file, function)` 组合逐个尝试。
- 若没有定位结果，则回退到 dataset hint 给出的函数。

## 7.4 命令行

```bash
# 全量处理 dataset 下所有 issue
python pipeline/generate.py

# 只处理单个 issue
python pipeline/generate.py 84608

# 强制覆盖已有结果
python pipeline/generate.py 84608 -f
```

> 说明：该脚本 CLI 本质上是“单 issue 或全量”，不支持 `localize.py` 那种逗号分隔多 issue 输入。

## 7.5 关键环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LAB_DATASET_DIR` | 外部注入 | 数据集目录 |
| `LAB_FIX_DIR` | 无默认（必需） | 生成结果输出目录 |
| `LAB_LLM_TOKEN` | 无默认（必需） | LLM token |
| `LAB_LLM_URL` | `https://api.deepseek.com` | LLM base URL |
| `LAB_LLM_MODEL` | `deepseek-reasoner` | 模型名 |
| `LAB_LLM_TEMP` | `0.8` | 温度 |
| `LAB_LLM_MAX_SAMPLE_COUNT` | `4` | 最大迭代轮数 |
| `LAB_LLM_MAX_LOG_SIZE` | `1000000000` | 日志截断上限 |
| `LAB_LLM_OMIT_ISSUE_BODY` | `ON` | 是否省略 issue body |
| `LAB_LLM_ENABLE_TOOLING` | `OFF` | 是否允许模型调用工具函数 |
| `LAB_LLM_ENABLE_STREAMING` | `OFF` | 是否流式响应 |
| `LAB_PROMPT_TYPE` | `cot` 分支 | `direct` / `cot` 提示模板 |
| `LAB_LLM_BASEMODEL_CUTOFF` | `1970-12-31Z` | Env 初始化参数 |
| `LAB_MAX_BUILD_JOBS` | `os.cpu_count()` | 编译并行数 |
| `LAB_LLVM_DIR` | 可选 | 用于 clone 源模板 |
| `LAB_LOCALIZE_OUTPUT` | 可选 | 定位输出 JSON 路径 |

## 7.6 可选“工具调用”能力

开启 `LAB_LLM_ENABLE_TOOLING=ON` 后，模型可调用：

- `get_source`：读取 LLVM 源码片段
- `get_instruction_docs`：获取指令/intrinsic 文档
- `check_refinement`：Alive2 检查语义等价

注意：`generate.py` 中 `get_source` 的路径检查写成了 `file.contains("..")`，这在 Python `str` 上不存在；该错误会在工具分发层被捕获并作为错误字符串返回，通常不会导致主流程崩溃，但会影响该工具可用性。

## 7.7 输出结果

输出是 `Env.dump(...)` 的 JSON（结构由 `lab_env.py` 定义），并可能附加：

- `fast_full_mismatch_patch`
- `fast_full_mismatch_full_check_count`

用于保存“fast 过 / full 挂”时的候选 patch，供后续复测或再修复。

---

## 8. `generate_orig.py` 详解（orig 数据集）

`generate_orig.py` 和 `generate.py` 框架几乎一致，但针对 `-orig` 数据集有关键差异。

## 8.1 核心差异

1. 脚本开头会强制将 `LAB_DATASET_DIR` 指向项目根的 `dataset/`。
2. 使用 `EnvironmentOrig(Env)`：
   - `check_fast()` / `check_full()` 使用 `llvm_helper.verify_test_group_orig(...)`
   - 注释写明：**test_body 为空、无 FileCheck**，主要靠 `opt + alive2 + cost`。
3. 结果目录是 `LAB_FIX_DIR_ORIG`（而非 `LAB_FIX_DIR`）。
4. 默认任务列表仅包含文件名中带 `orig` 的 dataset 条目。

## 8.2 命令行行为

```bash
# 全量处理所有 *orig*.json
python pipeline/generate_orig.py

# 显式传 -orig
python pipeline/generate_orig.py 85250-orig

# 传 base id 时会尝试映射到 <id>-orig
python pipeline/generate_orig.py 85250

# 强制覆盖
python pipeline/generate_orig.py 85250-orig -f
```

脚本对“同 id 的普通样本 + orig 样本同时存在”有去重提醒，会建议显式传 `-orig` 版本。

## 8.3 环境变量重点

与 `generate.py` 基本相同，关键区别：

- 输出目录改为 `LAB_FIX_DIR_ORIG`（必需）
- 数据集目录在脚本内部会被重置为项目下 `dataset`

---

## 9. `generalize.py` 详解

## 9.1 作用

给定一个 missed optimization 样例，让模型输出：

- 优化模式总结（summary / pattern / preconditions）
- 多组新测试（`testcases`）
- 每个测试的 `ir` 与 `expected_optimized_ir`

然后脚本会自动验证这些新测试是否真的体现 missed optimization。

## 9.2 处理对象

`generalize.py` 不是全量遍历：

- 只加载 **dataset 中第一个可用 issue**。
- “可用”判定：能找到满足条件的 test（优先找 `current != expected` 的）。

## 9.3 验证逻辑

对每个模型生成 testcase：

1. `opt --passes=instcombine -S` 跑当前优化结果。
2. `llvm_cost_tool` 计算 `expected` 与 `current_opt` 成本。
3. Alive2 检查：
   - `source -> expected`（确保期望优化本身合法）
   - `opt_out <-> expected` 双向检查（判断是否等价）
4. `missed_optimization` 判定条件（简化）：
   - 期望成本不高于当前优化结果
   - `source -> expected` 成立
   - 当前优化与期望不等价

## 9.4 命令行

```bash
python pipeline/generalize.py
```

该脚本不带 issue 参数，直接按默认逻辑处理第一个可用样本。

## 9.5 关键环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LAB_DATASET_DIR` | `dataset` | 数据集目录 |
| `LAB_GENERALIZE_OUTPUT` | `results/generalize/<model>.json` | 结果输出 |
| `LAB_LLM_TOKEN` / `LAB_LLM_URL` / `LAB_LLM_MODEL` / `LAB_LLM_TEMP` | 见代码默认 | 模型配置 |
| `LAB_LLM_BASEMODEL_CUTOFF` | `1970-12-31Z` | Env 初始化参数 |
| `LAB_MAX_BUILD_JOBS` | CPU 核数 | 编译并行数 |

## 9.6 输出结构（简化）

```json
{
  "issue_id": "84608",
  "dataset_file": "84608.json",
  "prompt": "...",
  "model_output": "...",
  "parsed": {"summary": "...", "testcases": [...]},
  "build": {"ok": true, "log": "..."},
  "validation": [
    {
      "name": "case_1",
      "missed_optimization": true,
      "cost": {"expected": 3, "current_opt": 5, "expected_le_current_opt": true, "ok": true},
      "source_to_expected_ok": true,
      "opt_equivalent_expected": false,
      "alive2": {"src_to_expected": "...", "opt_to_expected": "...", "expected_to_opt": "..."}
    }
  ]
}
```

---

## 10. `retest_patches.py` 详解

## 10.1 作用

对已有 patch 结果进行再验证，确保历史结论稳定。

默认策略：

- 从 `results/generate/fixes-gemini-3-cot-iter4-orig` 读取 patch JSON。
- 仅处理 `fast_check_pass == true` 的条目。
- 若 `full_check_pass == false`，优先取 `fast_full_mismatch_patch`。
- 若 `full_check_pass == true`，取 `patch`。
- 应用 patch 后跑 `env.check_fast(skip_build=False)`。

## 10.2 命令行

```bash
# 按默认 patch 目录批量复测
python pipeline/retest_patches.py

# 指定 issue（逗号分隔），支持 -orig 形式
python pipeline/retest_patches.py 85250-orig,76128-orig

# 强制覆盖输出
python pipeline/retest_patches.py 85250-orig,76128-orig -f
```

## 10.3 关键环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LAB_PATCH_DIR` | `results/generate/fixes-gemini-3-cot-iter4-orig` | patch 输入目录 |
| `LAB_RETEST_DIR` | `results/generate/retest-fixes-gemini-3-cot-iter4-orig` | 复测输出目录 |
| `LAB_DATASET_DIR` | `<project>/dataset` | 数据集目录 |
| `LAB_LLVM_DIR` | 必需 | LLVM 源目录（也用于 clone 模板） |
| `LAB_LLVM_BUILD_DIR` | 必需 | build 根目录 |
| `LAB_LLVM_ALIVE_TV` | 必需 | Alive2 工具 |
| `LAB_LLVM_COST_TOOL` | 必需 | 成本工具 |
| `LAB_LLM_BASEMODEL_CUTOFF` | `1970-12-31Z` | Env 初始化参数 |
| `LAB_MAX_BUILD_JOBS` | CPU 核数 | 编译并行数 |

## 10.4 结果内容

每个复测结果一般包含：

- patch 来源文件
- patch issue id 与 test issue id（会处理 `-orig` 映射）
- patch apply 成功与日志
- 二次 `check_fast` 成功与日志
- `env.dump(...)` 合并后的完整日志

---

## 11. 一套可直接执行的示例

假设你在仓库根目录，且已完成环境初始化：

```bash
# 1) 定位（先跑一个 issue）
python pipeline/localize.py 84608 -f

# 2) 生成补丁（普通样本）
python pipeline/generate.py 84608 -f

# 3) 若是 orig 数据集
python pipeline/generate_orig.py 84608-orig -f

# 4) 复测刚生成的补丁
LAB_PATCH_DIR=results/generate/fixes-gemini-3-cot-iter4-orig \
LAB_RETEST_DIR=results/generate/retest-manual \
python pipeline/retest_patches.py 84608-orig -f

# 5) 泛化分析（可选）
python pipeline/generalize.py
```

---

## 12. 常见问题与排查

## 12.1 localize 输出为空 / 解析失败

- 检查模型是否按要求输出 ` ```text ` 代码块。
- 减小 `LAB_LOCALIZE_MAX_FILE_CHARS`，避免上下文过长导致格式漂移。

## 12.2 generate 长期不收敛

- 先确认 `LAB_LOCALIZE_OUTPUT` 是否有效，避免函数定位偏差。
- 适当增大 `LAB_LLM_MAX_SAMPLE_COUNT`。
- 检查是否频繁进入“build failure”路径，必要时减小 prompt 复杂度。

## 12.3 retest 直接退出

- 优先检查 `LAB_LLVM_*` 相关环境变量是否都设置。
- 确认 patch JSON 中 `fast_check_pass` 是否为 `true`（否则会被跳过）。

## 12.4 generalize 看起来“只跑了一个 issue”

- 属于脚本当前设计行为：只取 dataset 首个可用样本。

---

## 13. 设计特点与局限（维护者视角）

### 特点

- 通过 `Env` 封装 LLVM build/check，流程一致性强。
- 反馈闭环完整：编译失败、优化失败、行为回归三类都能给模型定向反馈。
- localize + generate 解耦，便于替换定位器或模型。

### 局限

- `generate*.py` 的代码替换使用 `str.replace`，若 hunk 重复出现可能误替换多处。
- localize 候选文件是固定白名单，跨组件问题召回能力受限。
- `generalize.py` 目前只处理一个 issue，不适合直接做大规模统计。
- `generate.py` 的 `get_source` 工具路径校验有实现瑕疵（`file.contains`）。

---

## 14. 最小化实践建议

如果你只想快速得到可用结果，建议：

1. 先用 `localize.py` 跑出稳定的 `pred_files/pred_funcs`。
2. 再用 `generate.py`（或 `generate_orig.py`）单 issue 跑 `-f`，观察收敛。
3. 对可疑 patch 一律跑 `retest_patches.py` 二次确认。
4. 对关键 issue 用 `generalize.py` 扩展边界样例，防止“修了一个、漏了一类”。

---

## 15. 架构与设计（更细）

## 15.1 分层结构

这套 pipeline 可以抽象成 5 层：

1. **数据层**：`dataset/*.json`（issue、tests、hints、commands）。
2. **环境层**：`lab_env.Environment`（reset/build/check/dump、缓存目录、issue 元信息）。
3. **工具层**：`llvm_helper`（apply patch、alive2、verify、cost、git 操作、opt/lit 调用）。
4. **策略层**：`localize/generate/generate_orig/generalize/retest_patches` 的调度策略。
5. **模型层**：LLM prompt 构建、输出解析、失败反馈闭环。

## 15.2 设计目标

- 让模型**先定位再修复**，减少“盲改”范围。
- 用 LLVM 真实 build/check 作为硬约束，而不是只看文本相似度。
- 在失败路径上提供结构化反馈，形成可迭代优化闭环。
- 将“修复生产”和“复测验证”拆分，便于离线重放历史 patch。

## 15.3 非目标（当前实现）

- 不做全库语义级静态分析（依赖模型 + 运行验证）。
- 不保证 patch 最小化（当前是字符串替换，非 AST 编辑）。
- `generalize.py` 不做全数据集批量统计（只处理首个可用样例）。

---

## 16. 端到端时序（全流程）

```text
dataset issue
   │
   ├─(可选) localize.py
   │      ├─模型选 Top3 文件
   │      └─模型选每个文件 Top3 函数
   │
   ├─generate.py / generate_orig.py
   │      ├─准备 issue 描述 + 函数 hunk
   │      ├─模型生成替换代码
   │      ├─应用 patch
   │      ├─build + fast/full 验证
   │      └─失败反馈回灌，循环多轮
   │
   ├─产出 patch 日志 (LAB_FIX_DIR / LAB_FIX_DIR_ORIG)
   │
   └─retest_patches.py
          ├─重放 patch
          ├─check_fast 复测
          └─写出复测结果
```

如果要做模式抽象，额外走：

```text
generalize.py: 单样例 -> 模型泛化 testcases -> opt/cost/alive2 验证
```

---

## 17. 方法级实现说明（按脚本）

> 这部分重点解释“方法做什么、输入输出是什么、副作用是什么”。

## 17.1 `localize.py` 方法地图

### 核心入口

| 方法 | 作用 | 输入 | 输出 | 关键点 |
|---|---|---|---|---|
| `main()` | 批量处理 issue，执行文件定位 + 函数定位 | `sys.argv`、dataset、LLVM 源码 | 写入 `LAB_LOCALIZE_OUTPUT` | 支持增量写入和 `-f` 覆盖 |
| `parse_args(argv)` | 解析 issue 列表和覆盖开关 | 命令行参数 | `(issue_ids, override)` | issue 支持逗号分隔 |

### 数据准备

| 方法 | 作用 | 关键逻辑 |
|---|---|---|
| `get_test_case_from_env(env)` | 取第一条 `current != expected` 的测试 | 作为 missed-opt 描述来源 |
| `format_issue_desc_from_programs(...)` | 组装 IR before/after 文本 | 会按 `MAX_ISSUE_DESC_CHARS` 截断 |
| `load_gold_file_and_funcs(issue_id)` | 从 dataset hints 读取 gold 文件与函数 | 仅用于统计 `file_correct/func_correct` |

### 定位与解析

| 方法 | 作用 | 关键逻辑 |
|---|---|---|
| `build_file_localize_prompt(...)` | 文件定位提示词模板 | 可注入 opt debug 上下文 |
| `build_func_localize_prompt(...)` | 函数定位提示词模板 | 将完整文件内容截断后送模型 |
| `call_model(...)` | 统一调用 LLM | system/user 双消息 |
| `extract_pred_files_from_model_output(...)` | 从模型回复提取 Top-3 文件 | 依赖 ```text 代码块 |
| `extract_pred_funcs_from_model_output(...)` | 提取 Top-3 函数 | 同上 |

### 解析辅助与评估

| 方法 | 作用 | 关键逻辑 |
|---|---|---|
| `get_full_path_rel_under_llvm_root(...)` | 通过 basename 在 LLVM 根下定位文件 | `rglob`，重名时按排序取首个 |
| `compute_file_correct(...)` | 判断文件定位是否命中 gold | 支持 basename 和相对路径比对 |
| `compute_func_correct(...)` | 判断函数定位是否命中 gold | 支持 `A::B` 与 `B` 的松弛匹配 |
| `get_opt_debug_output(...)` | 运行 opt 命令并附加 `-debug` | 调整 test 命令中的 opt 路径 |

## 17.2 `generate.py` 方法地图（普通样本）

### A. 模型交互与消息管理

| 方法 | 作用 | 关键点 |
|---|---|---|
| `chat(...)` | 统一入口，选择 streaming 或 tooling 模式 | 由环境变量控制 |
| `chat_with_tooling(...)` | 支持函数调用的对话循环 | tool call -> dispatch -> 回填 tool 消息 |
| `chat_with_streaming(...)` | 流式接收模型输出 | 可记录 reasoning_content |
| `append_message(...)` | 同时维护 messages 与 full_messages | `full_messages` 用于最终 dump |

### B. 工具函数（开启 `LAB_LLM_ENABLE_TOOLING=ON` 时）

| 方法 | 作用 |
|---|---|
| `tool_get_source` | 返回 LLVM 源码指定行起的 10 行片段 |
| `tool_get_instruction_docs` | 查询 LangRef 指令/内建文档 |
| `tool_check_refinement` | 调 Alive2 验证 `src -> tgt` |
| `dispatch_tool_call` | 工具路由与参数反序列化 |

### C. 函数定位与 hunk 提取（与 localize 联动）

| 方法 | 作用 | 关键点 |
|---|---|---|
| `load_func_info_from_json(...)` | 读取 localize 结果 | 用 `LAB_LOCALIZE_OUTPUT` 指定路径 |
| `get_functions_to_try(issue_id)` | 生成待尝试 `(file, func)` 列表 | 优先匹配 gold，再 fallback 全 pred |
| `get_hunk_from_func(...)` | 从基线 commit 源码抽取函数体 | tree-sitter 优先，regex 兜底 |
| `truncate_hunk(...)` | 长函数二次裁剪 | 调模型选“最可能要改”的连续片段 |

### D. patch 应用与反馈闭环

| 方法 | 作用 | 关键点 |
|---|---|---|
| `extract_code_from_reply(...)` | 从模型回复提取代码块 | 优先最后一个 ```cpp 代码块 |
| `modify_inplace(file, src, tgt)` | 原位替换源码 | `str.replace`，不是结构化编辑 |
| `issue_fixing_iter(...)` | 单轮修复迭代 | 生成 -> 应用 -> `check_full` -> 反馈 |
| `test_is_successfully_optimized(...)` | 判定测试是否优化成功 | 优先 `result`，再 IR equality，最后 cost 兜底 |
| `_extract_cost_fields(...)` | 解析 test_log 中 cost 字段 | 防止日志结构不完整时崩溃 |
| `_append_missing_cost_feedback(...)` | cost 字段缺失时补充反馈 | 追加原始日志片段 |

### E. 证据记录

| 方法 | 作用 |
|---|---|
| `_record_fast_full_mismatch_patch(...)` | 记录 fast-pass/full-fail patch |
| `_maybe_attach_fast_full_mismatch_patch(...)` | 将上述 patch 附加到证书 JSON |
| `_dump_with_fast_full_patch(...)` | 统一 dump 包装 |
| `fix_issue(issue_id)` | 单 issue 主流程调度 |

## 17.3 `generate_orig.py` 方法地图（orig 样本）

`generate_orig.py` 与 `generate.py` 主体一致，关键差异在验证策略与任务筛选。

### `EnvironmentOrig` 重写方法

| 方法 | 与普通版区别 |
|---|---|
| `check_fast(skip_build=False)` | 调 `verify_test_group_orig`，不依赖 FileCheck test_body |
| `check_full()` | 先 `verify_test_group_orig`，再 `verify_lit` |

### 其它差异

- 输出目录用 `LAB_FIX_DIR_ORIG`。
- 默认任务集合仅包含文件名带 `orig` 的 dataset 条目。
- CLI 会尝试把普通 id 映射到 `<id>-orig`，并处理重复问题。

## 17.4 `generalize.py` 方法地图

| 方法 | 作用 | 关键点 |
|---|---|---|
| `_load_first_dataset_item(...)` | 从 dataset 挑首个可用样本 | 不是全量处理 |
| `_find_first_testcase(...)` | 在 issue 内找测试用例 | 优先有 `current_optimized_program` 且 `current != expected` |
| `_call_model(prompt)` | 调模型输出 JSON | 受 `MODEL_NAME/TEMPERATURE` 控制 |
| `_extract_json_obj(text)` | 从模型文本中容错提取 JSON | strict parse + 正则兜底 |
| `_run_opt_instcombine(ir)` | 调 `opt --passes=instcombine -S` | 返回 `(ok, stdout/err)` |
| `_run_cost_model(ir)` | 调 cost tool 取 `Cost:` 数值 | 失败时返回详细错误 |
| `_alive2_check(src, tgt)` | 调 Alive2 做 refinement 检查 | 自动补 ptr datalayout |
| `main()` | 构建 + 批量验证 testcases | 写出 `validation` 数组 |

## 17.5 `retest_patches.py` 方法地图

| 方法 | 作用 | 关键点 |
|---|---|---|
| `parse_args(...)` | 解析 issue 与 `-f` | issue 支持逗号分隔 |
| `resolve_patch_files(...)` | 收集 patch JSON 文件列表 | 支持 `.json` / `-orig` 多种输入形态 |
| `resolve_test_issue_id(...)` | patch issue 与 dataset issue 对齐 | 自动处理 `-orig` 映射 |
| `ensure_llvm_clone_for_issue(...)` | 准备 issue 专属 llvm clone | `utils/llvm-<issue>` |
| `retest_patch(...)` | 单 patch 复测主流程 | 筛选 fast_check_pass，选 patch 字段并重测 |
| `write_json(...)` | 安全落盘结果 | 自动创建目录 |
| `main()` | 批量调度复测 | 输出到 `LAB_RETEST_DIR` |

---

## 18. 关键判定逻辑（避免误解）

## 18.1 generate 的“优化成功”判定

`test_is_successfully_optimized` 采用三层策略：

1. 若 `test_log.result` 是布尔值，直接使用（最高优先级）。
2. 否则比较 `current_optimized_program` 与 `expect_optimized_program` 文本是否一致。
3. 再否则才退回 cost 近似判定（`curc < srcc` 或 `curc <= expc`）。

这比纯成本判定更稳健，能减少“成本变好但语义/模式不对”的误判。

## 18.2 generalize 的 `missed_optimization` 判定

简化条件：

- 期望 IR 成本 <= 当前 opt 输出成本；
- `source -> expected` 通过 Alive2（期望变换合法）；
- `opt_out` 与 `expected` **不等价**（双向 Alive2 失败或单侧失败）。

满足后标记为 missed optimization。

## 18.3 localize 的正确率统计

- `file_correct`：预测文件是否命中 gold 文件（支持 basename 或 resolved path）。
- `func_correct`：预测函数是否命中 gold 函数（支持命名空间后缀匹配）。

---

## 19. 输入/输出契约（Schema 视角）

## 19.1 数据集最小输入（示意）

```json
{
  "verified": true,
  "tests": [
    {
      "tests": [
        {
          "test_name": "...",
          "source_program": "...",
          "current_optimized_program": "...",
          "expect_optimized_program": "..."
        }
      ],
      "commands": ["opt ..."]
    }
  ],
  "hints": {
    "components": ["InstCombine"],
    "bug_location_funcname": {
      "llvm/lib/.../InstCombineXxx.cpp": ["InstCombinerImpl::visitXxx"]
    }
  },
  "lit_test_dir": ["llvm/test/.../"]
}
```

## 19.2 `localize.py` 输出

- `pred_files`: `list[str] | None`
- `pred_funcs`: `dict[str, list[str] | None]`
- `gold_file`, `gold_funcs`
- `file_correct`, `func_correct`

## 19.3 `generate*.py` 输出

主要来自 `env.dump(...)`，常见含义：

- 模型消息日志（含迭代上下文）
- build/fast/full 检查统计与日志
- patch 内容
- 可选 `fast_full_mismatch_patch`（当 fast 过、full 挂时）

## 19.4 `retest_patches.py` 输出

包含：

- patch 来源文件
- patch issue 与 test issue 映射
- patch apply 结果
- check_fast 复测结果
- `env.dump` 合并结果

## 19.5 `generalize.py` 输出

包含：

- `prompt` / `model_output` / `parsed`
- `build` 状态
- 每个 testcase 的 `validation`（cost + alive2 + missed_optimization）

---

## 20. 调参与性能建议（实践向）

## 20.1 影响速度的主要旋钮

1. `LAB_MAX_BUILD_JOBS`：编译并行度。
2. `LAB_LLM_MAX_SAMPLE_COUNT`：每个 issue 的迭代次数。
3. `LAB_LOCALIZE_ENABLE_OPT_DEBUG`：是否额外跑 debug 获取上下文。
4. `LAB_LOCALIZE_MAX_FILE_CHARS`：函数定位时传给模型的文件长度。

## 20.2 影响稳定性的主要旋钮

1. `LAB_LLM_TEMP`：温度过高会导致输出格式漂移。
2. `LAB_PROMPT_TYPE`：`direct` 与 `cot` 对可解析性和可读性有不同影响。
3. `LAB_LLM_MAX_LOG_SIZE`：日志截断太小可能丢关键信息。

## 20.3 建议的起步配置

- 先低温（如 `0.2~0.6`）跑稳定格式；
- 小批 issue 验证流程后再全量；
- 先开 localize，再跑 generate；
- 对成功 patch 必做 retest。

---

## 21. 故障排查矩阵（按症状）

| 症状 | 可能原因 | 处理建议 |
|---|---|---|
| localize 无预测结果 | 模型未按 ```text 格式输出 | 降温、缩短上下文、检查 prompt 约束 |
| generate 一直 build fail | API/补丁不兼容、替换片段错误 | 聚焦编译日志，减小 hunk，增加轮次 |
| generate fast 过 full 挂 | 回归测试被破坏 | 检查 `fast_full_mismatch_patch`，补充约束 |
| retest 全部跳过 | `fast_check_pass != true` 或找不到 patch 文件 | 检查 patch JSON 条件与路径 |
| retest 启动即退出 | `LAB_LLVM_*` 环境不全 | 先 source 环境脚本 |
| generalize 结果为空 | 首个样本不满足条件、模型输出非 JSON | 检查 dataset 与模型输出格式 |

---

## 22. 已知行为与风险点

1. **字符串替换风险**：`modify_inplace` 用 `str.replace`，若 hunk 非唯一可能误替换多处。
2. **定位歧义**：basename 解析在重名文件场景下可能命中错误路径。
3. **格式依赖**：localize/generalize 强依赖模型输出格式（`text`/JSON），格式漂移会降级。
4. **`generalize.py` 范围有限**：默认只处理首个可用 issue，不代表全数据集分布。
5. **工具调用实现细节**：`generate.py` 里 `tool_get_source` 使用了 `file.contains("..")`，会导致该路径检查逻辑不可用（仅 tooling 分支受影响）。

---

## 23. 二次开发指南

## 23.1 想提升定位召回率

- 扩展 `localize.py` 的 `CANDIDATE_FILES`。
- 增加“按组件动态候选文件集”。
- 将 basename 匹配升级为“相对路径 + 语义索引”双通道。

## 23.2 想提升 patch 质量

- 用 AST 级替换代替 `str.replace`。
- 引入多候选 patch 并行评估（best-of-N）。
- 在反馈中加入更细粒度统计（哪条 lit 失败、哪个 alive2 case 失败）。

## 23.3 想做离线评测

- 固定模型参数 + 固定 seed（如供应商支持）。
- 按 issue 记录：localize 命中、generate 成功轮数、retest 通过率。
- 分普通/orig 两条线分别统计，避免混淆验证口径。

---

## 24. 一页式执行清单（推荐）

```bash
# 0) 初始化环境（示例）
# source init_env.sh

# 1) 先做定位
python pipeline/localize.py 84608 -f

# 2) 再做修复（普通）
python pipeline/generate.py 84608 -f

# 3) 或修复（orig）
python pipeline/generate_orig.py 84608-orig -f

# 4) 复测 patch
python pipeline/retest_patches.py 84608-orig -f

# 5) 模式泛化（可选）
python pipeline/generalize.py
```

执行顺序建议始终是：**定位 -> 生成 -> 复测 -> 泛化（可选）**。
