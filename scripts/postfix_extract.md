# postfix_extract.py 说明

## 1. 功能定位
`postfix_extract.py` 是数据集构建的核心抽取器：
- 输入一个 LLVM issue id；
- 自动定位修复提交；
- 提取 patch、hints、测试、issue 上下文；
- 生成标准样本 JSON。

---

## 2. 抽取流程总览
1. 拉取 issue 与 timeline。
2. 确定 `fix_commit`：
   - 先查人工 `fix_commit_map`；
   - 否则从 timeline 事件自动推断。
3. 推导 `base_commit = fix_commit~`。
4. 抽取代码 patch（`llvm/lib` + `llvm/include`）。
5. 计算 hints：
   - 组件级（路径推断）
   - 行级（hunk 行区间）
   - 函数级（tree-sitter 定位）
6. 抽取测试：
   - 从 `llvm/test` patch 提取 RUN/FileCheck 命令
   - 用 `llvm-extract-18` 抽取子函数测试体
7. 抽取 issue 正文、标签、评论（过滤噪声）。
8. 汇总 metadata 写入 `dataset/{issue_id}.json`。

---

## 3. 关键设计点

### 3.1 特殊映射 `fix_commit_map`
用于覆盖自动推断失败或不适合收录的 issue：
- `None`：标记无效样本
- commit hash：强制使用指定修复提交

### 3.2 测试抽取兼容策略
部分 issue 的测试在 fix 提交上不可直接提取，脚本允许从 `origin/main` 回退抓取测试文件（`retrieve_test_from_main`）。

### 3.3 结构化输出
最终 JSON 包含：
- `bug_id`、`issue_url`、`bug_type`
- `base_commit`、`knowledge_cutoff`
- `hints`、`patch`、`tests`、`issue`
- `properties`（单文件/单函数修复）

---

## 4. 使用方式

```bash
# 普通模式：若文件已存在则跳过
python3 scripts/postfix_extract.py <issue_id>

# 强制覆盖
python3 scripts/postfix_extract.py <issue_id> -f
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`
- `scripts/hints.py`
- `unidiff`、`requests`
- `llvm-extract` / `llvm-extract-18`

### 上下游关系
- 上游调用者：`scripts/extract_from_issues.py`
- 下游消费者：`scripts/dataset_postfix.py`、`scripts/verify_repro.py`、pipeline

---

## 6. 注意事项
1. 对外部工具和网络依赖较多，失败场景较复杂。
2. 会过滤多个标签类别（invalid/duplicate 等），并非所有 closed issue 都会入库。
3. 生成文件为事实来源，建议配合版本管理追踪变化。
