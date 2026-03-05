# Pipeline — LLVM InstCombine 自动优化修复流水线

本目录包含一套端到端的 **LLVM InstCombine missed-optimization 自动修复流水线**。给定一个 missed optimization 的 issue（以 `dataset/` 中的 JSON 描述），流水线依次完成 **定位 → 补丁生成 → 补丁复测** 三个阶段，利用 LLM 自动生成 C++ 补丁并通过编译、测试、语义验证闭环迭代。

> **注意**：模式泛化（generalize）阶段暂时不作为主流程的前置步骤使用；补丁生成阶段直接使用 `dataset/` 中每个 issue 自带的测试用例。

---

## 执行顺序总览

```
┌─────────────────────────────────────────────────────────────┐
│  0. 环境初始化  source init_env.sh <model>                   │
├─────────────────────────────────────────────────────────────┤
│  (1. generalize.py — 暂不使用，可独立运行)                    │
├─────────────────────────────────────────────────────────────┤
│  2. localize.py         — 定位：预测待修改的文件与函数          │
│        ↓ 输出 results/localize/<model>.json                  │
├─────────────────────────────────────────────────────────────┤
│  3. generate.py          — 补丁生成（有 FileCheck 的 issue）   │
│     generate_orig.py     — 补丁生成（无 FileCheck 的 issue）   │
│        ↓ 输出 results/generate/fixes-<model>-.../<id>.json   │
├─────────────────────────────────────────────────────────────┤
│  4. retest_patches.py   — 补丁复测：用 golden test cases 重测  │
│        ↓ 输出 results/generate/retest-.../<id>.json          │
└─────────────────────────────────────────────────────────────┘
```

---

## 0. 环境初始化

运行任何 pipeline 脚本前，需要先初始化环境：

```bash
source init_env.sh <model>
# model 可选: gpt-4.1 | gpt-4o | gemini-3 | deepseek-v3.2 | glm-4.7 | qwen-3 | sonnet-4.5
```

`init_env.sh` 负责：
- 设置 LLM API 凭据（`LAB_LLM_TOKEN`、`LAB_LLM_URL`、`LAB_LLM_MODEL`）
- 设置实验参数（`LAB_LLM_MAX_SAMPLE_COUNT`、`LAB_PROMPT_TYPE`）
- 配置路径（`LAB_LLVM_DIR`、`LAB_LLVM_BUILD_DIR`、`LAB_DATASET_DIR`、`LAB_FIX_DIR`、`LAB_LOCALIZE_OUTPUT` 等）
- 自动克隆 llvm-project、构建 Alive2 验证器、构建 cost 度量工具

---

## 1. 模式泛化 — `generalize.py`（暂不使用）

> **当前流程中暂时跳过此步骤**，直接使用 `dataset/` 中 issue 自带的唯一测试用例。此脚本可独立运行用于研究。

### 功能

从数据集中取出 **单个 missed optimization 示例**（`source_program` + `expect_optimized_program`），通过 LLM 泛化出更多测试用例，并逐一验证每个用例是否确实存在 missed optimization。

### 工作流程

1. 从 `dataset/` 加载第一个包含有效测试用例的 issue JSON
2. 构造 prompt，要求 LLM 输出 JSON 格式的泛化结果（包含 `testcases` 数组）
3. 构建 LLVM（通过 `Environment.reset()` + `Environment.build()`）
4. 对每个生成的 testcase 依次执行：
   - **opt --passes=instcombine**：验证 IR 语法合法
   - **cost model**：比较 expected IR 与当前 opt 输出的 IR 代价
   - **Alive2 验证**：检查 source → expected 的语义正确性，以及 opt_output ↔ expected 的等价性
5. 将验证结果写入 `results/generalize/<model>.json`

### 关键环境变量

| 变量 | 用途 |
|------|------|
| `LAB_DATASET_DIR` | 数据集目录 |
| `LAB_GENERALIZE_OUTPUT` | 输出路径（默认 `results/generalize/<model>.json`） |
| `LAB_LLM_MODEL` | LLM 模型名 |
| `LAB_LLM_TEMP` | 采样温度（默认 0.8） |

### 关联文件

- `scripts/llvm_helper.py` — LLVM 构建/测试/Alive2 验证的底层封装
- `scripts/lab_env.py` — `Environment` 类，管理 issue 的完整生命周期
- `dataset/<issue_id>.json` — 输入数据

---

## 2. 定位 — `localize.py`

### 功能

对每个 issue，让 LLM **预测应该修改的 InstCombine 源文件及其中的函数名**。该步骤的输出会被后续的 `generate.py` / `generate_orig.py` 读取，指导补丁生成时聚焦到正确的代码位置。

### 工作流程

1. 遍历 `dataset/` 中的所有 issue JSON
2. 对每个 issue：
   - 通过 `Environment` 获取 test case（找到 `current_optimized_program ≠ expect_optimized_program` 的用例）
   - 从 `dataset/<id>.json` 的 `hints.bug_location_funcname` 字段加载 **gold_file** 和 **gold_funcs**（用于评估）
   - **（可选）构建 LLVM 并运行 opt -debug**，获取 debug 输出作为额外上下文
   - **第一轮 LLM 调用 — 文件定位**：从 15 个预定义的 InstCombine 候选文件中，让模型选出最可能需要修改的 Top-3 文件
   - **第二轮 LLM 调用 — 函数定位**：对每个预测文件，读取其完整源码，让模型选出 Top-3 函数
3. 计算 `file_correct` 和 `func_correct`（与 gold 标签对比）
4. 结果写入 `results/localize/<model>.json`

### 候选文件列表

脚本中硬编码了 15 个 InstCombine 相关源文件作为候选（`CANDIDATE_FILES`），覆盖：
`InstCombineCalls.cpp`, `InstCombineSelect.cpp`, `InstCombineAddSub.cpp`, `InstCombineAndOrXor.cpp`, `InstCombineCompares.cpp` 等。

### 关键环境变量

| 变量 | 用途 |
|------|------|
| `LAB_LOCALIZE_OUTPUT` | 输出路径（默认 `results/localize/<model>.json`） |
| `LAB_LOCALIZE_LLVM_ROOT` | LLVM 源码根目录（用于读取候选文件内容） |
| `LAB_LOCALIZE_ENABLE_OPT_DEBUG` | 是否启用 opt -debug 输出（默认 ON） |
| `LAB_LOCALIZE_BUILD_BEFORE_OPT_DEBUG` | 是否在获取 debug 输出前先构建 LLVM（默认 ON） |
| `LAB_LOCALIZE_MAX_FILE_CHARS` | 文件内容截断阈值（默认 200000） |

### 用法

```bash
# 处理全部 issue
python pipeline/localize.py

# 处理指定 issue（逗号分隔）
python pipeline/localize.py 85250,76128

# 覆盖已有结果
python pipeline/localize.py 85250 -f
```

### 输出格式

```json
{
  "<issue_id>": {
    "pred_files": ["InstCombineXXX.cpp", ...],
    "pred_funcs": {"InstCombineXXX.cpp": ["funcA", "funcB", ...], ...},
    "gold_file": "llvm/lib/Transforms/InstCombine/InstCombineXXX.cpp",
    "gold_funcs": ["InstCombinerImpl::visitXxx"],
    "file_correct": true,
    "func_correct": true
  }
}
```

### 关联文件

- `scripts/llvm_helper.py` — Git 操作、LLVM 构建
- `scripts/lab_env.py` — `Environment` 类
- `dataset/<issue_id>.json` — 输入数据（含 `hints.bug_location_funcname`）
- **输出被 `generate.py` / `generate_orig.py` 消费**（通过 `LAB_LOCALIZE_OUTPUT`）

---

## 3. 补丁生成 — `generate.py` / `generate_orig.py`

### 功能

核心阶段。对每个 issue，让 LLM 在定位到的目标函数附近 **生成 C++ 代码补丁**，然后 **反复编译、测试、反馈迭代**，直到通过验证或达到最大迭代次数。

### 两个变体

| 脚本 | 适用场景 | 验证方式 | 输出目录 |
|------|---------|---------|---------|
| `generate.py` | 常规 issue（有 FileCheck test_body） | `env.check_full()` = 构建 + FileCheck + llvm-lit | `LAB_FIX_DIR` |
| `generate_orig.py` | `-orig` 后缀 issue（test_body 为空） | `env.check_full()` = 构建 + opt + Alive2 + cost（无 FileCheck） | `LAB_FIX_DIR_ORIG` |

`generate_orig.py` 定义了 `EnvironmentOrig(Env)` 子类，覆写了 `check_fast()` 和 `check_full()` 方法，使用 `llvm_helper.verify_test_group_orig()` 代替 FileCheck。

### 工作流程

1. 读取 localize 阶段的输出（`LAB_LOCALIZE_OUTPUT`），获取 `pred_funcs`（预测的文件+函数列表）
2. 对每个 issue：
   - 为 issue 克隆独立的 llvm-project 副本到 `utils/llvm-<issue_id>/`
   - 初始化 `Environment`，重置到 `base_commit`
   - 首次构建 LLVM 并缓存（`build_cache`），后续迭代从缓存恢复
   - 运行 `check_fast()` 确认未修复状态
   - 从 localize 结果中获取要尝试的 `(file, func)` 对列表
   - 对每个 `(file, func)` 对：
     - 通过 tree-sitter 或正则从 git 历史中提取目标函数源码
     - 对过长函数调用 LLM 进行智能截断（`truncate_hunk`）
     - 构造 prompt：issue 描述 + 函数代码 + 格式要求（CoT 或 direct）
     - **迭代循环**（默认最多 4 轮 `LAB_LLM_MAX_SAMPLE_COUNT`）：
       1. LLM 生成补丁代码
       2. 从回复中提取 ```cpp 代码块
       3. 替换 LLVM 源码中的目标函数
       4. 编译 + 完整检查（`check_full`）
       5. 若通过 → 保存成功结果并退出
       6. 若失败 → 根据失败类型构造反馈：
          - **编译失败**：提取关键错误信息
          - **fast_check 失败**：展示当前优化结果 vs 期望结果，附 Alive2 日志
          - **full_check 失败**（fast 通过但 lit 失败）：展示回归测试失败日志
       7. 将反馈追加到对话历史，进入下一轮
3. 将最终结果（含对话历史、patch、通过/失败状态）写入 `results/generate/fixes-<model>-<prompt>-iter<N>/<issue_id>.json`

### LLM 工具调用

脚本注册了 3 个可选工具（通过 `LAB_LLM_ENABLE_TOOLING=ON` 启用）：
- **`get_source`** — 查看 LLVM 源码指定位置
- **`get_instruction_docs`** — 查询 LLVM IR 指令/intrinsic 文档
- **`check_refinement`** — 调用 Alive2 验证变换正确性

### 关键环境变量

| 变量 | 用途 |
|------|------|
| `LAB_FIX_DIR` | generate.py 输出目录 |
| `LAB_FIX_DIR_ORIG` | generate_orig.py 输出目录 |
| `LAB_LOCALIZE_OUTPUT` | localize 阶段结果路径（被 generate 读取） |
| `LAB_LLM_MAX_SAMPLE_COUNT` | 最大迭代轮数（默认 4） |
| `LAB_PROMPT_TYPE` | `cot`（链式思考）或 `direct`（直接输出） |
| `LAB_LLM_ENABLE_TOOLING` | 是否启用 LLM 工具调用（默认 OFF） |
| `LAB_LLM_ENABLE_STREAMING` | 是否启用流式输出（默认 OFF） |
| `LAB_LLM_OMIT_ISSUE_BODY` | 是否省略 issue body（默认 ON） |

### 用法

```bash
# 处理全部 issue
python pipeline/generate.py

# 处理指定 issue
python pipeline/generate.py 85250

# 覆盖已有结果
python pipeline/generate.py 85250 -f

# orig 变体（无 FileCheck 的 issue）
python pipeline/generate_orig.py 85250-orig
```

### 输出格式

每个 issue 生成一个 JSON 文件，关键字段：
- `fast_check_pass` — 快速检查是否通过
- `full_check_pass` — 完整检查是否通过
- `patch` — 最终 git diff 补丁
- `fast_full_mismatch_patch` — fast 通过但 full 未通过时的补丁备份
- `log.messages` — 完整的 LLM 对话历史

### 关联文件

- `scripts/llvm_helper.py` — Git 操作、LLVM 构建/测试、Alive2、cost model
- `scripts/lab_env.py` — `Environment` 类（`reset`, `build`, `check_fast`, `check_full`, `dump`）
- `results/localize/<model>.json` — **输入**：localize 阶段输出的 `pred_funcs`
- `dataset/<issue_id>.json` — 输入数据
- `utils/llvm-<issue_id>/` — 每个 issue 独立的 LLVM 克隆

---

## 4. 补丁复测 — `retest_patches.py`

### 功能

对 generate 阶段产生的历史补丁进行 **统一重测**，用 developer 的 golden test cases 来验证补丁的可复现性。主要用于：
- 在不同环境下验证补丁是否仍然有效
- 使用更新的 dataset（可能包含更多测试用例）重新评估
- 区分 `fast_check_pass` 但 `full_check_pass` 失败的情况

### 工作流程

1. 从补丁目录（默认 `results/generate/fixes-gemini-3-cot-iter4-orig`）读取 JSON 补丁文件
2. 对每个补丁文件：
   - **过滤**：仅处理 `fast_check_pass == true` 的补丁
   - **选择补丁字段**：
     - 若 `full_check_pass == true` → 使用 `patch` 字段
     - 若 `full_check_pass == false` → 使用 `fast_full_mismatch_patch` 字段
   - **匹配 dataset**：从 patch issue id（如 `85250-orig`）映射到对应的 `dataset/85250.json`
   - 为 issue 克隆独立 LLVM 副本，初始化 `Environment`
   - 验证 issue 已被 verified
   - 应用补丁（`llvm_helper.apply(patch)`）
   - 运行 `env.check_fast(skip_build=False)` 重新验证
3. 将结果写入 `results/generate/retest-.../<issue_id>.json`

### 关键环境变量

| 变量 | 用途 |
|------|------|
| `LAB_PATCH_DIR` | 输入补丁目录 |
| `LAB_RETEST_DIR` | 输出结果目录 |
| `LAB_LLVM_DIR` | LLVM 源码目录 |
| `LAB_LLVM_BUILD_DIR` | LLVM 构建目录 |

### 用法

```bash
# 重测全部补丁
python pipeline/retest_patches.py

# 重测指定补丁
python pipeline/retest_patches.py 85250-orig,76128-orig

# 覆盖已有结果
python pipeline/retest_patches.py 85250-orig -f
```

### 输出格式

```json
{
  "patch_source_file": "...",
  "patch_issue_id": "85250-orig",
  "test_issue_id": "85250",
  "source_fast_check_pass": true,
  "patch_apply": {"ok": true, "log": "..."},
  "check_more": {"ok": true, "log": "..."}
}
```

### 关联文件

- `scripts/llvm_helper.py` — `apply()` 补丁应用、构建、测试
- `scripts/lab_env.py` — `Environment` 类
- `results/generate/fixes-*/<id>.json` — **输入**：generate 阶段产生的补丁
- `dataset/<issue_id>.json` — golden test cases

---

## 目录结构

```
pipeline/
├── README.md              ← 本文件
├── generalize.py          ← (暂不使用) 模式泛化：扩展测试用例
├── localize.py            ← 步骤 2：定位待修改的文件和函数
├── generate.py            ← 步骤 3：补丁生成（有 FileCheck）
├── generate_orig.py       ← 步骤 3：补丁生成（无 FileCheck / orig 变体）
└── retest_patches.py      ← 步骤 4：补丁复测
```

## 关联的外部目录与文件

| 路径 | 说明 |
|------|------|
| `init_env.sh` | 环境初始化脚本，设置所有必要环境变量 |
| `scripts/llvm_helper.py` | LLVM 操作底层库（git、cmake、ninja、alive2、cost model） |
| `scripts/lab_env.py` | `Environment` 类，封装单个 issue 的生命周期管理 |
| `dataset/` | 输入数据集，每个 issue 一个 JSON 文件 |
| `results/localize/` | localize 阶段输出 |
| `results/generate/` | generate 阶段输出（补丁 + retest 结果） |
| `utils/llvm/llvm-project/` | 共享的 LLVM 源码仓库 |
| `utils/llvm-<issue_id>/` | 每个 issue 独立的 LLVM 克隆 |
| `utils/alive2/` | Alive2 语义验证工具 |
| `utils/cost/` | IR 代价度量工具 |
| `build/` | LLVM 构建目录（含 `<issue_id>_cache` 缓存） |

## 数据流图

```
dataset/<id>.json
       │
       ├──→ localize.py ──→ results/localize/<model>.json
       │                              │
       ├──────────────────────────────┤
       │                              ↓
       ├──→ generate.py ──────→ results/generate/fixes-<model>-.../<id>.json
       │    generate_orig.py                     │
       │                                         ↓
       └──→ retest_patches.py ──→ results/generate/retest-.../<id>.json
```
